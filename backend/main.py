from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import Dict
import os
from dotenv import load_dotenv
import vertexai
from vertexai.generative_models import GenerativeModel

# Load environment variables from .env file
load_dotenv()

app = FastAPI()

PROJECT_ID = os.getenv("PROJECT_ID")
REGION = os.getenv("REGION")

class ChatRequest(BaseModel):
    message: str
    user_id: str

class ApprovalRequest(BaseModel):
    branch: str
    action: str  # 'approve' or 'reject'
    user_id: str

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
@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/chat")
def chat(req: ChatRequest):
    try:
        terraform_code = call_vertex_ai(req.message)
        return {"response": terraform_code}
    except Exception as e:
        return {"response": f"Error: {str(e)}"}

@app.post("/approve")
def approve(req: ApprovalRequest):
    # TODO: Handle approval/rejection logic
    return {"result": "[Placeholder]"}
