from fastapi import FastAPI, Request
from pydantic import BaseModel
from typing import Dict
import os
from dotenv import load_dotenv
import vertexai
from vertexai.generative_models import GenerativeModel
from github import Github
import datetime
import difflib
from unidiff import PatchSet
import io

# Load environment variables from .env file
load_dotenv()

app = FastAPI()

PROJECT_ID = os.getenv("PROJECT_ID")
REGION = os.getenv("REGION")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
DEFAULT_BRANCH = os.getenv("DEFAULT_BRANCH", "main")

# In-memory store for generated Terraform change and context per user
user_terraform_change = {}
user_terraform_context = {}

class ChatRequest(BaseModel):
    message: str
    user_id: str

class ApprovalRequest(BaseModel):
    user_id: str
    action: str  # 'approve' or 'reject'


def fetch_terraform_files():
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(GITHUB_REPO)
    branch = repo.get_branch(DEFAULT_BRANCH)
    tree = repo.get_git_tree(branch.commit.sha, recursive=True).tree
    tf_files = [f for f in tree if (f.path.endswith('.tf') or f.path.endswith('.tfvars')) and f.type == 'blob']
    # Limit to 25 files, 100 KB each
    files = []
    for f in tf_files[:25]:
        blob = repo.get_git_blob(f.sha)
        content = blob.content
        import base64
        decoded = base64.b64decode(content)
        if len(decoded) <= 100 * 1024:
            files.append({"path": f.path, "content": decoded.decode(errors='replace')})
    return files

def call_vertex_ai_with_context(prompt: str, files: list) -> str:
    vertexai.init(project=PROJECT_ID, location=REGION)
    model = GenerativeModel("gemini-2.0-flash-lite-001")
    context = "\n\n".join([f"File: {f['path']}\n{f['content']}" for f in files])
    full_prompt = (
        f"You are an expert DevOps assistant. Here is the current state of the infrastructure as Terraform files:\n"
        f"{context}\n\n"
        f"User request: {prompt}\n\n"
        f"Output ONLY a valid unified diff (as produced by `git diff`), suitable for direct application with `patch` or `git apply`. "
        f"Do not omit any lines, hunk headers, or context. Do not include explanations, comments, or extra text.\n\n"
        f"Format your response as:\n"
        f"Summary: <summary here>\n"
        f"Change:\n```diff\n<diff or patch here>\n```\n\n"
        f"For example, if the user request is \"Change the instance type from n1-standard-1 to n1-standard-2 in main.tf\", and the relevant lines in main.tf are:\n\n"
        f"resource \"google_compute_instance\" \"default\" {{\n  name         = \"test\"\n  machine_type = \"n1-standard-1\"\n  ...\n}}\n\n"
        f"The diff should look like:\n\n"
        f"diff --git a/main.tf b/main.tf\nindex 1234567..89abcde 100644\n--- a/main.tf\n+++ b/main.tf\n@@ -2,7 +2,7 @@\n resource \"google_compute_instance\" \"default\" {{\n   name         = \"test\"\n-  machine_type = \"n1-standard-1\"\n+  machine_type = \"n1-standard-2\"\n   ...\n }}\n\n"
        f"If multiple files are changed, include all changes in the same diff."
    )
    response = model.generate_content(full_prompt)
    return response.text

def extract_change(response: str) -> str:
    import re
    # Try to extract diff from code block after Change:
    match = re.search(r"Change:\s*```diff([\s\S]*?)```", response)
    if match:
        return match.group(1).strip()
    # Fallback: try to extract any diff code block
    match = re.search(r"```diff([\s\S]*?)```", response)
    if match:
        return match.group(1).strip()
    # Fallback: try to extract a raw unified diff from the response
    # Look for the first occurrence of a diff header
    diff_start = re.search(r"^diff --git .*$|^--- a/.*$", response, re.MULTILINE)
    if diff_start:
        return response[diff_start.start():].strip()
    # If nothing found, return empty string
    return ""

def apply_diff_to_files(files, diff_text):
    """
    Apply a unified diff to a list of files (dicts with 'path' and 'content').
    Returns a dict of updated file contents {path: new_content}.
    """
    # Remove '\ No newline at end of file' lines
    diff_text = '\n'.join(line for line in diff_text.splitlines() if line.strip() != '\\ No newline at end of file')
    file_map = {f['path']: f['content'].splitlines(keepends=True) for f in files}
    updated_files = {path: ''.join(lines) for path, lines in file_map.items()}
    patch = PatchSet(io.StringIO(diff_text))
    for patched_file in patch:
        # Remove a/ or b/ prefix for matching
        path = patched_file.path
        if path.startswith('a/') or path.startswith('b/'):
            path = path[2:]
        if path not in file_map:
            continue
        print(f"[DEBUG] File '{path}' has {len(file_map[path])} lines.")
        for hunk in patched_file:
            print(f"[DEBUG] Hunk header: source_start={hunk.source_start}, source_length={hunk.source_length}, target_start={hunk.target_start}, target_length={hunk.target_length}")
        original = file_map[path]
        new_lines = []
        i = 0
        for hunk in patched_file:
            # Add unchanged lines before the hunk
            while i < hunk.source_start - 1:
                new_lines.append(original[i])
                i += 1
            # Apply hunk
            for line in hunk:
                if line.is_added:
                    new_lines.append(line.value)
                elif line.is_context:
                    new_lines.append(original[i])
                    i += 1
                elif line.is_removed:
                    i += 1
            # After hunk, i is at the next line to process
        # Add any remaining lines after the last hunk
        new_lines.extend(original[i:])
        updated_files[path] = ''.join(new_lines)
    return updated_files

def parse_changed_files_and_summary(response: str):
    """
    Parse the model's response and extract (summary, {filename: new_content})
    Expects format:
    Summary: ...\nFile: <filename>\n```terraform\n<new file content>\n```\n(Repeat for each changed file)
    """
    import re
    summary_match = re.search(r'^Summary:(.*)$', response, re.MULTILINE)
    summary = summary_match.group(1).strip() if summary_match else None
    files = {}
    # Strictly match: File: <filename>\n```(terraform)?\n<content>\n```
    file_blocks = re.findall(r'^File: (.*?)\n```(?:terraform)?\n([\s\S]*?)\n```', response, re.MULTILINE)
    for filename, content in file_blocks:
        files[filename.strip()] = content.strip()
    return summary, files

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/chat")
def chat(req: ChatRequest):
    try:
        files = fetch_terraform_files()
        print(f"[DEBUG] Files fetched for context: {[f['path'] for f in files]}")
        response = call_vertex_ai_with_context(req.message, files)
        change = extract_change(response)
        user_terraform_change[req.user_id] = change
        user_terraform_context[req.user_id] = files
        return {"response": response}
    except Exception as e:
        return {"response": f"Error: {str(e)}"}

@app.post("/approve")
def approve(req: ApprovalRequest):
    if req.action == "approve":
        response = user_terraform_change.get(req.user_id)
        files = user_terraform_context.get(req.user_id)
        if not response or not files:
            return {"result": "No change or context found for this user. Please generate a change first."}
        print(f"[DEBUG] Model response for user {req.user_id}:\n{response}")
        try:
            summary, changed_files = parse_changed_files_and_summary(response)
            if not changed_files:
                return {"result": "No files to change. The model did not output any file changes.", "summary": summary}
        except Exception as e:
            return {"result": f"Error parsing model response: {e}"}
        try:
            g = Github(GITHUB_TOKEN)
            repo = g.get_repo(GITHUB_REPO)
            base = repo.get_branch(DEFAULT_BRANCH)
            branch_name = f"infra-change-{req.user_id}-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
            repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base.commit.sha)
            commit_message = f"Apply infrastructure change for user {req.user_id} via chatbot"
            for path, content in changed_files.items():
                # If file exists, update; else, create
                try:
                    f = repo.get_contents(path, ref=branch_name)
                    repo.update_file(path, commit_message, content, f.sha, branch=branch_name)
                except Exception:
                    repo.create_file(path, commit_message, content, branch=branch_name)
            pr = repo.create_pull(
                title=f"Infra change for user {req.user_id}",
                body="Automated PR from GCP Terraform Chatbot" + (f"\n\nSummary: {summary}" if summary else ""),
                head=branch_name,
                base=DEFAULT_BRANCH
            )
            return {"result": f"Pull request created: {pr.html_url}", "summary": summary}
        except Exception as e:
            return {"result": f"Error creating PR: {str(e)}"}
    else:
        user_terraform_change.pop(req.user_id, None)
        user_terraform_context.pop(req.user_id, None)
        return {"result": "Request rejected and change discarded."}
