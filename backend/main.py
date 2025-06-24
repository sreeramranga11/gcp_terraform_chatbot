from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import Dict
import os
from dotenv import load_dotenv
import vertexai
from vertexai.generative_models import GenerativeModel
from github import Github
import datetime

# Load environment variables from .env file
load_dotenv()

app = FastAPI()

PROJECT_ID = os.getenv("PROJECT_ID")
REGION = os.getenv("REGION")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")

# In-memory store for generated Terraform code per user
user_terraform_code = {}

class ChatRequest(BaseModel):
    message: str
    user_id: str

class ApprovalRequest(BaseModel):
    user_id: str
    action: str  # 'approve' or 'reject'

def call_vertex_ai(prompt: str) -> str:
    vertexai.init(project=PROJECT_ID, location=REGION)
    model = GenerativeModel("gemini-2.0-flash-lite-001")
    # Compose a better prompt
    full_prompt = (
        f"Generate a concise summary (1-2 sentences) in plain English describing the requested GCP infrastructure change, "
        f"and then provide only the Terraform code block to accomplish it. "
        f"User request: {prompt}\n"
        f"Format your response as:\n"
        f"Summary: <summary here>\n"
        f"Terraform:\n```hcl\n<terraform code here>\n```\n"
        f"Do not include bash scripts, gcloud commands, or lengthy explanations."
    )
    response = model.generate_content(full_prompt)
    return response.text

def extract_terraform_code(response: str) -> str:
    import re
    match = re.search(r"Terraform:\s*```hcl([\s\S]*?)```", response)
    if match:
        return match.group(1).strip()
    return ""

def create_github_pr(terraform_code: str, user_id: str) -> str:
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(GITHUB_REPO)
    base = repo.get_branch("main")
    # Unique branch name
    branch_name = f"infra-change-{user_id}-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base.commit.sha)
    # Write Terraform code to main.tf
    file_path = f"terraform_{user_id}.tf"
    commit_message = f"Add Terraform code for user {user_id} via chatbot"
    repo.create_file(file_path, commit_message, terraform_code, branch=branch_name)
    pr = repo.create_pull(
        title=f"Infra change for user {user_id}",
        body="Automated PR from GCP Terraform Chatbot",
        head=branch_name,
        base="main"
    )
    return pr.html_url

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/chat")
def chat(req: ChatRequest):
    try:
        response = call_vertex_ai(req.message)
        terraform_code = extract_terraform_code(response)
        user_terraform_code[req.user_id] = terraform_code
        return {"response": response}
    except Exception as e:
        return {"response": f"Error: {str(e)}"}

@app.post("/approve")
def approve(req: ApprovalRequest):
    if req.action == "approve":
        terraform_code = user_terraform_code.get(req.user_id)
        if not terraform_code:
            return {"result": "No Terraform code found for this user. Please generate code first."}
        try:
            pr_url = create_github_pr(terraform_code, req.user_id)
            return {"result": f"Pull request created: {pr_url}"}
        except Exception as e:
            return {"result": f"Error creating PR: {str(e)}"}
    else:
        user_terraform_code.pop(req.user_id, None)
        return {"result": "Request rejected and code discarded."}
