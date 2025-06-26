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

def call_vertex_ai(prompt: str) -> str:
    vertexai.init(project=PROJECT_ID, location=REGION)
    model = GenerativeModel("gemini-2.0-flash-lite-001")
    response = model.generate_content(prompt)
    return response.text

def initial_summary_and_diff(user_prompt: str, files: list) -> str:
    context = "\n\n".join([f"File: {f['path']}\n{f['content']}" for f in files])
    full_prompt = (
        f"You are an expert DevOps assistant. Here is the current state of the infrastructure as Terraform files:\n"
        f"{context}\n\n"
        f"User request: {user_prompt}\n\n"
        f"Output ONLY a concise summary and a valid unified diff (as from `git diff`).\n"
        f"Format your response as:\n"
        f"Summary: <summary here>\n"
        f"Change:\n```diff\n<diff or patch here>\n```\n\n"
        f"Do not include explanations, comments, or extra text."
    )
    return call_vertex_ai(full_prompt)

def cleanup_diff(diff: str) -> str:
    prompt = (
        f"Here is a proposed unified diff. Clean it up to ensure it is a valid, patchable unified diff, with correct headers, context lines, and no extra text. Output only the cleaned diff.\n"
        f"```diff\n{diff}\n```"
    )
    return call_vertex_ai(prompt)

def validate_and_fix_diff(diff: str, files: list) -> str:
    context = "\n\n".join([f"File: {f['path']}\n{f['content']}" for f in files])
    prompt = (
        f"Here is the current state of the infrastructure as Terraform files:\n"
        f"{context}\n\n"
        f"Here is a unified diff:\n````diff\n{diff}\n````\n"
        f"Is this diff valid and patchable? If not, fix it and output only the corrected diff. Output only the valid unified diff, nothing else."
    )
    return call_vertex_ai(prompt)

def apply_diff_to_files(files, diff_text):
    """
    Apply a unified diff to a list of files (dicts with 'path' and 'content').
    Returns a dict of updated file contents {path: new_content}.
    """
    print("[DEBUG] Raw diff_text received:")
    print(diff_text)
    # Remove '\ No newline at end of file' lines
    diff_text = '\n'.join(line for line in diff_text.splitlines() if line.strip() != '\\ No newline at end of file')
    file_map = {f['path']: f['content'].splitlines(keepends=True) for f in files}
    updated_files = {path: ''.join(lines) for path, lines in file_map.items()}
    print(f"[DEBUG] Files in repo context: {list(file_map.keys())}")
    patch = PatchSet(io.StringIO(diff_text))
    diff_files = [patched_file.path for patched_file in patch]
    print(f"[DEBUG] Files found in diff: {diff_files}")
    for patched_file in patch:
        # Remove a/ or b/ prefix for matching
        path = patched_file.path
        if path.startswith('a/') or path.startswith('b/'):
            path = path[2:]
        if path not in file_map:
            print(f"[DEBUG] Creating new file from diff: {path}")
            # New file: build content from added and context lines in the diff
            new_lines = []
            for hunk in patched_file:
                for line in hunk:
                    if line.is_added or line.is_context:
                        new_lines.append(line.value)
            updated_files[path] = ''.join(new_lines)
            continue
        print(f"[DEBUG] Updating existing file '{path}' with {len(file_map[path])} original lines.")
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
    print(f"[DEBUG] Updated files to be returned: {list(updated_files.keys())}")
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
        response = initial_summary_and_diff(req.message, files)
        print(f"[DEBUG] Initial model response (summary+diff):\n{response}")
        # Extract summary and diff
        import re
        summary_match = re.search(r"Summary:(.*?)(Change:|$)", response, re.DOTALL)
        summary = summary_match.group(1).strip() if summary_match else None
        diff_match = re.search(r"Change:\s*```diff([\s\S]*?)```", response)
        diff = diff_match.group(1).strip() if diff_match else None
        print(f"[DEBUG] Extracted summary: {summary}")
        print(f"[DEBUG] Extracted diff:\n{diff}")
        user_terraform_change[req.user_id] = diff
        user_terraform_context[req.user_id] = files
        user_terraform_summary = summary
        return {"response": response, "summary": summary, "diff": diff}
    except Exception as e:
        print(f"[ERROR] Exception in /chat: {e}")
        return {"response": f"Error: {str(e)}"}

@app.post("/approve")
def approve(req: ApprovalRequest):
    if req.action == "approve":
        diff = user_terraform_change.get(req.user_id)
        files = user_terraform_context.get(req.user_id)
        if not diff or not files:
            print("[DEBUG] No change or context found for this user.")
            return {"result": "No change or context found for this user. Please generate a change first."}
        print(f"[DEBUG] Initial diff for user {req.user_id}:\n{diff}")
        # Step 2: Clean up diff
        cleaned_diff = cleanup_diff(diff)
        print(f"[DEBUG] Cleaned diff (after cleanup prompt):\n{cleaned_diff}")
        # Step 3: Validate/fix diff
        validated_diff = validate_and_fix_diff(cleaned_diff, files)
        print(f"[DEBUG] Validated/fixed diff (after validation prompt):\n{validated_diff}")
        # Step 4: Apply diff and push
        try:
            print(f"[DEBUG] Applying validated diff to files...")
            updated_files = apply_diff_to_files(files, validated_diff)
            print(f"[DEBUG] Files after applying diff: {list(updated_files.keys())}")
        except Exception as e:
            print(f"[ERROR] Error applying diff: {e}")
            return {"result": f"Error applying diff: {e}"}
        try:
            print(f"[DEBUG] Preparing to push changes to GitHub...")
            g = Github(GITHUB_TOKEN)
            repo = g.get_repo(GITHUB_REPO)
            base = repo.get_branch(DEFAULT_BRANCH)
            branch_name = f"infra-change-{req.user_id}-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
            repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base.commit.sha)
            commit_message = f"Apply infrastructure change for user {req.user_id} via chatbot"
            for path, content in updated_files.items():
                print(f"[DEBUG] Committing file: {path}")
                # If file exists, update; else, create
                try:
                    f = repo.get_contents(path, ref=branch_name)
                    repo.update_file(path, commit_message, content, f.sha, branch=branch_name)
                except Exception:
                    repo.create_file(path, commit_message, content, branch=branch_name)
            pr = repo.create_pull(
                title=f"Infra change for user {req.user_id}",
                body="Automated PR from GCP Terraform Chatbot",
                head=branch_name,
                base=DEFAULT_BRANCH
            )
            print(f"[DEBUG] Pull request created: {pr.html_url}")
            return {"result": f"Pull request created: {pr.html_url}"}
        except Exception as e:
            print(f"[ERROR] Error creating PR: {e}")
            return {"result": f"Error creating PR: {str(e)}"}
    else:
        user_terraform_change.pop(req.user_id, None)
        user_terraform_context.pop(req.user_id, None)
        print("[DEBUG] Request rejected and change discarded.")
        return {"result": "Request rejected and change discarded."}
