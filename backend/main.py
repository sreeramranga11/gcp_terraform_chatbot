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
import re
import json
import requests

# Load environment variables from .env file
load_dotenv()

app = FastAPI()

PROJECT_ID = os.getenv("PROJECT_ID")
REGION = os.getenv("REGION")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")
DEFAULT_BRANCH = os.getenv("DEFAULT_BRANCH", "main")
JIRA_URL = os.getenv("JIRA_URL")  # e.g., https://yourdomain.atlassian.net
JIRA_USER = os.getenv("JIRA_USER")  # email or username
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")

# In-memory store for generated Terraform change and context per user
user_terraform_change = {}
user_terraform_context = {}

class ChatRequest(BaseModel):
    message: str
    user_id: str

class ApprovalRequest(BaseModel):
    user_id: str
    action: str  # 'approve' or 'reject'

class SummarizeRequest(BaseModel):
    user_id: str


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
        f"IMPORTANT: First determine if this is actually a request for infrastructure changes or just casual conversation. If it is casual conversation, respond with a friendly message\n"
        f"User request: {user_prompt}\n\n"
        f"For each file that needs to be changed, output only the full, updated content for each changed block (resource/module/variable/etc.), with clear file and block identifiers.\n"
        f"Use this format for each change:\n"
        f"File: <filename>\nBlock: <block identifier or resource name>\n```hcl\n<full new block content>\n```\n"
        f"Repeat for each changed block in each file.\n"
        f"Do NOT include explanations, comments, or extra text."
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
        f"STRICT: Output ONLY a valid, patchable unified diff for all changed files. Do NOT include any 'File: ...' blocks, explanations, or extra text. Only the diff. Ensure there are blank lines between file diffs. Double-check that all hunk headers, line numbers, and context match the current file content exactly. If you are unsure, output a full file diff that replaces the entire file, with correct hunk headers and context."
    )
    return call_vertex_ai(prompt)

def apply_diff_to_files(files, diff_text):
    """
    Apply a unified diff to a list of files (dicts with 'path' and 'content').
    Returns a dict of updated file contents {path: new_content}.
    Handles multi-file diffs robustly: tries PatchSet on the whole diff, falls back to per-file splitting if needed.
    """
    print("[DEBUG] Raw validated diff_text to be applied:")
    print(diff_text)
    # Remove markdown code block markers if present
    diff_text = re.sub(r'^```diff\s*|```$', '', diff_text.strip(), flags=re.MULTILINE)
    # Remove '\ No newline at end of file' lines
    diff_text = '\n'.join(line for line in diff_text.splitlines() if line.strip() != '\ No newline at end of file')
    file_map = {f['path']: f['content'].splitlines(keepends=True) for f in files}
    updated_files = {path: ''.join(lines) for path, lines in file_map.items()}
    print(f"[DEBUG] Files in repo context: {list(file_map.keys())}")

    try:
        patch = PatchSet(io.StringIO(diff_text))
        print(f"[DEBUG] PatchSet parsed {len(patch)} files from unified diff.")
        for patched_file in patch:
            # Remove a/ or b/ prefix for matching
            path = patched_file.path
            if path.startswith('a/') or path.startswith('b/'):
                path = path[2:]
            file_exists = path in file_map
            print(f"[DEBUG] Processing file: {path} (exists in repo: {file_exists})")
            if file_exists:
                print(f"[DEBUG] File '{path}' length: {len(file_map[path])} lines.")
            else:
                print(f"[DEBUG] File '{path}' does not exist in repo context. Will be created if diff applies.")
            for hunk in patched_file:
                print(f"[DEBUG] Hunk header for {path}: source_start={hunk.source_start}, source_length={hunk.source_length}, target_start={hunk.target_start}, target_length={hunk.target_length}")
            if not file_exists:
                print(f"[DEBUG] Creating new file from diff: {path}")
                # New file: build content from added and context lines in the diff
                new_lines = []
                for hunk in patched_file:
                    for line in hunk:
                        if line.is_added or line.is_context:
                            new_lines.append(line.value)
                updated_files[path] = ''.join(new_lines)
                print(f"[DEBUG] New file created: {path}")
                continue
            original = file_map[path]
            new_lines = []
            i = 0
            try:
                for hunk in patched_file:
                    print(f"[DEBUG] Applying hunk to {path}: file length={len(original)}, hunk source_start={hunk.source_start}, hunk source_length={hunk.source_length}")
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
                print(f"[DEBUG] Updated file: {path}")
            except IndexError as e:
                print(f"[ERROR] IndexError applying hunk in file {path}: {e}")
                print(f"[ERROR] Falling back to full file replacement for {path} using all added/context lines from diff.")
                fallback_lines = []
                for hunk in patched_file:
                    for line in hunk:
                        # Only include actual code lines, not diff headers or file markers
                        if (line.is_added or line.is_context) and not (
                            line.value.strip().startswith('--- a/') or
                            line.value.strip().startswith('+++ b/') or
                            line.value.strip().startswith('@@') or
                            line.value.strip().startswith('File:')
                        ):
                            fallback_lines.append(line.value)
                updated_files[path] = ''.join(fallback_lines)
                print(f"[DEBUG] Fallback content for {path} (first 500 chars):\n{updated_files[path][:500]}")
        print(f"[DEBUG] Updated files to be returned: {list(updated_files.keys())}")
        return updated_files
    except Exception as e:
        print(f"[ERROR] PatchSet failed on whole diff: {e}")
        print("[DEBUG] Falling back to per-file diff splitting.")
        # Fallback: Pre-process and split diff into per-file chunks
        file_diffs = re.split(r'(?=^--- a/)', diff_text, flags=re.MULTILINE)
        for file_diff in file_diffs:
            file_diff = file_diff.strip()
            if not file_diff:
                continue
            print(f"[DEBUG] Processing file diff chunk:\n{file_diff[:500]}\n--- END CHUNK ---")
            try:
                patch = PatchSet(io.StringIO(file_diff))
            except Exception as e:
                print(f"[ERROR] PatchSet parse error for chunk: {e}")
                continue
            for patched_file in patch:
                # Remove a/ or b/ prefix for matching
                path = patched_file.path
                if path.startswith('a/') or path.startswith('b/'):
                    path = path[2:]
                file_exists = path in file_map
                print(f"[DEBUG] (Fallback) Processing file: {path} (exists in repo: {file_exists})")
                if file_exists:
                    print(f"[DEBUG] (Fallback) File '{path}' length: {len(file_map[path])} lines.")
                else:
                    print(f"[DEBUG] (Fallback) File '{path}' does not exist in repo context. Will be created if diff applies.")
                for hunk in patched_file:
                    print(f"[DEBUG] (Fallback) Hunk header for {path}: source_start={hunk.source_start}, source_length={hunk.source_length}, target_start={hunk.target_start}, target_length={hunk.target_length}")
                if not file_exists:
                    print(f"[DEBUG] (Fallback) Creating new file from diff: {path}")
                    new_lines = []
                    for hunk in patched_file:
                        for line in hunk:
                            if line.is_added or line.is_context:
                                new_lines.append(line.value)
                    updated_files[path] = ''.join(new_lines)
                    print(f"[DEBUG] (Fallback) New file created: {path}")
                    continue
                original = file_map[path]
                new_lines = []
                i = 0
                try:
                    for hunk in patched_file:
                        print(f"[DEBUG] (Fallback) Applying hunk to {path}: file length={len(original)}, hunk source_start={hunk.source_start}, hunk source_length={hunk.source_length}")
                        while i < hunk.source_start - 1:
                            new_lines.append(original[i])
                            i += 1
                        for line in hunk:
                            if line.is_added:
                                new_lines.append(line.value)
                            elif line.is_context:
                                new_lines.append(original[i])
                                i += 1
                            elif line.is_removed:
                                i += 1
                    new_lines.extend(original[i:])
                    updated_files[path] = ''.join(new_lines)
                    print(f"[DEBUG] (Fallback) Updated file: {path}")
                except IndexError as e:
                    print(f"[ERROR] (Fallback) IndexError applying hunk in file {path}: {e}")
                    print(f"[ERROR] (Fallback) Falling back to full file replacement for {path} using all added/context lines from diff.")
                    fallback_lines = []
                    for hunk in patched_file:
                        for line in hunk:
                            # Only include actual code lines, not diff headers or file markers
                            if (line.is_added or line.is_context) and not (
                                line.value.strip().startswith('--- a/') or
                                line.value.strip().startswith('+++ b/') or
                                line.value.strip().startswith('@@') or
                                line.value.strip().startswith('File:')
                            ):
                                fallback_lines.append(line.value)
                    updated_files[path] = ''.join(fallback_lines)
                    print(f"[DEBUG] (Fallback) Fallback content for {path} (first 500 chars):\n{updated_files[path][:500]}")
        print(f"[DEBUG] (Fallback) Updated files to be returned: {list(updated_files.keys())}")
        return updated_files

def parse_changed_files_and_summary(response: str):
    """
    Parse the model's response and extract (summary, {filename: new_content})
    Expects format:
    Summary: ...\nFile: <filename>\n```terraform\n<new file content>\n```\n(Repeat for each changed file)
    """
    summary_match = re.search(r'^Summary:(.*)$', response, re.MULTILINE)
    summary = summary_match.group(1).strip() if summary_match else None
    files = {}
    # Strictly match: File: <filename>\n```(terraform)?\n<content>\n```
    file_blocks = re.findall(r'^File: (.*?)\n```(?:terraform)?\n([\s\S]*?)\n```', response, re.MULTILINE)
    for filename, content in file_blocks:
        files[filename.strip()] = content.strip()
    return summary, files

# Helper: parse model response for block changes
def parse_block_changes(response: str):
    """
    Parse model response for block changes in the format:
    File: <filename>\nBlock: <block identifier>\n```hcl\n<block content>\n```\n
    Returns: dict {filename: list of (block_id, block_content)}
    """
    changes = {}
    pattern = r'File: (.*?)\nBlock: (.*?)\n```hcl\n([\s\S]*?)```'
    for match in re.finditer(pattern, response):
        filename = match.group(1).strip()
        block_id = match.group(2).strip()
        block_content = match.group(3).strip()
        if filename not in changes:
            changes[filename] = []
        changes[filename].append((block_id, block_content))
    return changes

def find_block_span(file_content, block_header):
    """
    Returns (start, end) indices of the block in file_content, or None if not found.
    """
    import re
    header_pattern = re.compile(re.escape(block_header) + r'\s*\{', re.MULTILINE)
    match = header_pattern.search(file_content)
    if not match:
        return None
    start = match.start()
    i = match.end()  # position after the opening brace
    depth = 1
    while i < len(file_content):
        if file_content[i] == '{':
            depth += 1
        elif file_content[i] == '}':
            depth -= 1
            if depth == 0:
                return (start, i + 1)
        i += 1
    return None  # Block not closed properly


def replace_or_insert_block(file_content, block_id, new_block):
    import re
    print(f"[DEBUG] Attempting to match block_id: {block_id}")
    print(f"[DEBUG] File content preview:\n{file_content[:200]}")
    # 1. Try to match assignment: block_id = [ or block_id = {
    assign_pattern = re.compile(rf'^{re.escape(block_id)}\s*=\s*([\[\{{])', re.MULTILINE)
    assign_match = assign_pattern.search(file_content)
    if assign_match:
        open_bracket = assign_match.group(1)
        close_bracket = ']' if open_bracket == '[' else '}'
        start = assign_match.start()
        i = assign_match.end()
        depth = 1
        while i < len(file_content):
            if file_content[i] == open_bracket:
                depth += 1
            elif file_content[i] == close_bracket:
                depth -= 1
                if depth == 0:
                    end = i + 1
                    new_content = file_content[:start] + new_block + '\n' + file_content[end:]
                    print(f"[DEBUG] Replaced assignment '{block_id}' in file (bracket-counting for assignment).")
                    return new_content
            i += 1
        print(f"[DEBUG] Assignment header found but not closed properly for '{block_id}'. Appending at end.")
        return file_content.rstrip() + '\n\n' + new_block + '\n'
    # 2. Try to match block header: block_id { (as before)
    block_header_pattern = re.compile(rf'^{re.escape(block_id)}\s*\{{', re.MULTILINE)
    match = block_header_pattern.search(file_content)
    if match:
        start = match.start()
        i = match.end()
        depth = 1
        while i < len(file_content):
            if file_content[i] == '{':
                depth += 1
            elif file_content[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    new_content = file_content[:start] + new_block + '\n' + file_content[end:]
                    print(f"[DEBUG] Replaced block '{block_id}' in file (bracket-counting, robust header match).")
                    return new_content
            i += 1
        print(f"[DEBUG] Block header found but block not closed properly for '{block_id}'. Appending at end.")
        return file_content.rstrip() + '\n\n' + new_block + '\n'
    else:
        print(f"[DEBUG] Block or assignment header for '{block_id}' not found, inserting at end of file.")
        return file_content.rstrip() + '\n\n' + new_block + '\n'

# Main patch-by-block logic
def apply_block_changes(files, block_changes):
    """
    files: list of dicts with 'path' and 'content'
    block_changes: dict {filename: list of (block_id, block_content)}
    Returns: dict {filename: new_content}
    """
    updated_files = {f['path']: f['content'] for f in files}
    for filename, changes in block_changes.items():
        if filename not in updated_files:
            print(f"[DEBUG] File '{filename}' not found in repo context, skipping.")
            continue
        content = updated_files[filename]
        for block_id, new_block in changes:
            print(f"[DEBUG] Applying block change: file={filename}, block_id={block_id}")
            content = replace_or_insert_block(content, block_id, new_block)
        updated_files[filename] = content
    print(f"[DEBUG] Updated files after block changes: {list(updated_files.keys())}")
    return updated_files

# Helper to transition a Jira issue
def jira_transition_issue(issue_key, transition_name):
    print(f"[JIRA] Transitioning {issue_key} to '{transition_name}'...")
    # Get all transitions
    url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}/transitions"
    auth = (JIRA_USER, JIRA_API_TOKEN)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    resp = requests.get(url, auth=auth, headers=headers)
    if resp.status_code != 200:
        print(f"[JIRA] Failed to get transitions: {resp.text}")
        return False
    transitions = resp.json().get("transitions", [])
    tid = None
    for t in transitions:
        if t["name"].lower() == transition_name.lower():
            tid = t["id"]
            break
    if not tid:
        print(f"[JIRA] Transition '{transition_name}' not found for {issue_key}.")
        return False
    # Do the transition
    resp = requests.post(url, auth=auth, headers=headers, json={"transition": {"id": tid}})
    print(f"[JIRA] Transition response: {resp.status_code} {resp.text}")
    return resp.status_code == 204

# Helper to comment on a Jira issue
def jira_comment_issue(issue_key, comment):
    print(f"[JIRA] Commenting on {issue_key}...")
    url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}/comment"
    auth = (JIRA_USER, JIRA_API_TOKEN)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    # Jira Cloud requires Atlassian Document Format (ADF)
    body = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": comment
                        }
                    ]
                }
            ]
        }
    }
    resp = requests.post(url, auth=auth, headers=headers, json=body)
    print(f"[JIRA] Comment response: {resp.status_code} {resp.text}")
    return resp.status_code == 201

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/chat")
def chat(req: ChatRequest):
    try:
        files = fetch_terraform_files()
        print(f"[DEBUG] Files fetched for context: {[f['path'] for f in files]}")
        response = initial_summary_and_diff(req.message, files)
        print(f"[DEBUG] Initial model response (block changes):\n{response}")
        # Store the full response for approval
        user_terraform_change[req.user_id] = response
        user_terraform_context[req.user_id] = files
        return {"response": response}
    except Exception as e:
        print(f"[ERROR] Exception in /chat: {e}")
        return {"response": f"Error: {str(e)}"}

@app.post("/approve")
def approve(req: ApprovalRequest):
    print(f"[DEBUG] user_terraform_change keys: {list(user_terraform_change.keys())}")
    print(f"[DEBUG] user_terraform_context keys: {list(user_terraform_context.keys())}")
    print(f"[DEBUG] user_terraform_change for user {req.user_id}: {user_terraform_change.get(req.user_id)}")
    print(f"[DEBUG] user_terraform_context for user {req.user_id}: {user_terraform_context.get(req.user_id)}")
    if req.action == "approve":
        response = user_terraform_change.get(req.user_id)
        files = user_terraform_context.get(req.user_id)
        if not response or not files:
            print("[DEBUG] No change or context found for this user.")
            return {"result": "No change or context found for this user. Please generate a change first."}
        print(f"[DEBUG] Model response for user {req.user_id} (block changes):\n{response}")
        try:
            block_changes = parse_block_changes(response)
            if not block_changes:
                print("[DEBUG] No block changes parsed from model response.")
                return {"result": "No block changes found in model response."}
            updated_files = apply_block_changes(files, block_changes)
        except Exception as e:
            print(f"[ERROR] Error applying block changes: {e}")
            return {"result": f"Error applying block changes: {e}"}
        # Only push if any file content actually changed
        changed = False
        for f in files:
            orig = f['content']
            updated = updated_files.get(f['path'], orig)
            if orig != updated:
                changed = True
                break
        if not changed:
            print("[DEBUG] No actual file changes detected. Not creating PR.")
            return {"result": "No files were changed. The block changes could not be applied or resulted in no changes."}
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

@app.post("/summarize")
def summarize(req: SummarizeRequest):
    response = user_terraform_change.get(req.user_id)
    if not response:
        return {"summary": "No change found for this user."}
    prompt = (
        f"You are an expert DevOps assistant. Here is a set of Terraform block changes, each with a file and block name. "
        f"Summarize the overall infrastructure change in 1-2 sentences, focusing on what is being added, removed, or modified. "
        f"Do NOT include code, only a human-readable summary.\n\n"
        f"{response}"
    )
    summary = call_vertex_ai(prompt)
    return {"summary": summary.strip()}

@app.post("/webhook/jira")
async def jira_webhook(request: Request):
    print("[JIRA WEBHOOK] Endpoint hit!")
    payload = await request.json()
    print("[JIRA WEBHOOK] Received payload:", json.dumps(payload, indent=2))

    # 1. Parse event type
    event_type = request.headers.get("X-Atlassian-Webhook-Identifier") or payload.get("webhookEvent")
    print(f"[JIRA WEBHOOK] Event type: {event_type}")

    # 2. Only process issue_created events
    if payload.get("webhookEvent") != "jira:issue_created":
        print("[JIRA WEBHOOK] Ignoring non-issue_created event.")
        return {"status": "ignored", "reason": "not issue_created"}

    # 3. Extract ticket info
    issue = payload.get("issue", {})
    key = issue.get("key")
    fields = issue.get("fields", {})
    summary = fields.get("summary")
    description = fields.get("description")
    reporter = fields.get("reporter", {}).get("displayName")
    print(f"[JIRA WEBHOOK] Issue key: {key}")
    print(f"[JIRA WEBHOOK] Summary: {summary}")
    print(f"[JIRA WEBHOOK] Description: {description}")
    print(f"[JIRA WEBHOOK] Reporter: {reporter}")

    # Only process if status is 'To Do'
    status_name = fields.get("status", {}).get("name", "").lower()
    print(f"[JIRA WEBHOOK] Issue status: {status_name}")
    if status_name != "to do":
        print("[JIRA WEBHOOK] Ticket is not in 'To Do' status. Skipping agentic workflow.")
        return {"status": "ignored", "reason": "not in To Do"}

    try:
        # Move ticket to In Progress
        jira_transition_issue(key, "In Progress")

        user_prompt = summary or ""
        if description:
            user_prompt += f"\n{description}"
        print(f"[JIRA WEBHOOK] Using user_prompt: {user_prompt}")
        files = fetch_terraform_files()
        print(f"[JIRA WEBHOOK] Files fetched for context: {[f['path'] for f in files]}")
        response = initial_summary_and_diff(user_prompt, files)
        print(f"[JIRA WEBHOOK] Model response (block changes):\n{response}")
        user_terraform_change[key] = response
        user_terraform_context[key] = files

        # --- Apply changes and create PR (same as /approve logic) ---
        block_changes = parse_block_changes(response)
        if not block_changes:
            print("[JIRA WEBHOOK] No block changes parsed from model response.")
            return {"status": "no_changes", "reason": "No block changes found in model response."}
        updated_files = apply_block_changes(files, block_changes)
        changed = False
        for f in files:
            orig = f['content']
            updated = updated_files.get(f['path'], orig)
            if orig != updated:
                changed = True
                break
        if not changed:
            print("[JIRA WEBHOOK] No actual file changes detected. Not creating PR.")
            return {"status": "no_changes", "reason": "No files were changed. The block changes could not be applied or resulted in no changes."}
        print(f"[JIRA WEBHOOK] Preparing to push changes to GitHub...")
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(GITHUB_REPO)
        base = repo.get_branch(DEFAULT_BRANCH)
        branch_name = f"infra-change-{key}-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base.commit.sha)
        commit_message = f"Apply infrastructure change for Jira ticket {key} via chatbot"
        for path, content in updated_files.items():
            print(f"[JIRA WEBHOOK] Committing file: {path}")
            try:
                f = repo.get_contents(path, ref=branch_name)
                repo.update_file(path, commit_message, content, f.sha, branch=branch_name)
            except Exception:
                repo.create_file(path, commit_message, content, branch=branch_name)
        pr = repo.create_pull(
            title=f"Infra change for Jira ticket {key}",
            body=f"Automated PR from GCP Terraform Chatbot for Jira ticket {key}",
            head=branch_name,
            base=DEFAULT_BRANCH
        )
        print(f"[JIRA WEBHOOK] Pull request created: {pr.html_url}")

        # Move ticket to In Review and comment with summary and PR link
        summary_text = None
        try:
            prompt = (
                f"You are an expert DevOps assistant. Here is a set of Terraform block changes, each with a file and block name. "
                f"Summarize the overall infrastructure change in 1-2 sentences, focusing on what is being added, removed, or modified. "
                f"Do NOT include code, only a human-readable summary.\n\n"
                f"{response}"
            )
            summary_text = call_vertex_ai(prompt)
            print(f"[JIRA WEBHOOK] Summary for comment: {summary_text}")
        except Exception as e:
            print(f"[JIRA WEBHOOK] Error getting summary: {e}")
            summary_text = "(Could not generate summary)"
        jira_transition_issue(key, "In Review")
        comment = (
            f"Automated infrastructure change proposed for this ticket.\n\n"
            f"**Jira Ticket:** {key} - {summary}\n\n"
            f"**Summary of changes:**\n{summary_text}\n\n"
            f"**Review the proposed changes in this PR:** {pr.html_url}\n\n"
            f"If you have feedback or require changes, please comment here."
        )
        jira_comment_issue(key, comment)

        return {
            "status": "pr_created",
            "pr_url": pr.html_url,
            "issue_key": key,
            "summary": summary,
            "description": description,
            "reporter": reporter
        }
    except Exception as e:
        print(f"[JIRA WEBHOOK] Error in agentic workflow: {e}")
        return {"status": "error", "error": str(e)}
