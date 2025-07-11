# GCP Terraform Chatbot (Agentic Jira-Driven Version)

## Overview

This project is an **agentic DevOps assistant** that automates GCP infrastructure changes using Jira tickets as the trigger. When a user creates a Jira ticket, the agent:
- Picks up the ticket (if in "To Do" status)
- Moves it to **In Progress**
- Proposes Terraform changes using Vertex AI
- Creates a new branch and PR in GitHub
- Moves the ticket to **In Review** and comments with a summary and PR link
- Logs all actions to **Google Cloud Logging** for full auditability

No manual chat or approval is needed—**the entire flow is driven by Jira tickets**.

---

## Architecture

- **Backend:** FastAPI app that listens for Jira webhooks, runs the agentic workflow, manages GitHub PRs, and logs to GCP Logging.
- **Jira:** Used as the user interface for requesting and tracking infrastructure changes.
- **GitHub:** Stores Terraform code and receives automated PRs.
- **Vertex AI:** Generates Terraform code changes from natural language.
- **GCP Logging:** Stores all logs for traceability and debugging.

---

## Setup

### 1. **Jira Setup**

#### a. **Create a Jira API Token**
- Go to https://id.atlassian.com/manage-profile/security/api-tokens
- Click **Create API token**, label it, and copy the token.

#### b. **Create a Jira Webhook**
- Go to Jira Settings → System → Webhooks
- Click **Create a Webhook**
- **URL:** Use your public FastAPI endpoint (see ngrok below for local dev)
- **Events:** Select **Issue Created**
- **Filters:** (Recommended) Filter by project and status (e.g., only "To Do")
- **Status:** Enabled

#### c. **Jira Permissions**
- The API user must have permission to transition issues and add comments in the relevant project.

---

### 2. **GCP Logging Setup**

- The service account used by your backend must have the **Logs Writer** role (`roles/logging.logWriter`).
- Set the `GOOGLE_APPLICATION_CREDENTIALS` environment variable to the path of your service account key JSON.
- [IAM & Admin > IAM](https://console.cloud.google.com/iam-admin/iam) → Add role to your service account.

---

### 3. **ngrok for Local Development**

If running locally, expose your FastAPI server to the internet using ngrok:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
ngrok http 8000
```
- Use the HTTPS forwarding URL from ngrok (e.g., `https://abcd1234.ngrok.io/webhook/jira`) as your Jira webhook URL.

---

### 4. **Environment Variables (.env Example)**

Create a `.env` file in your backend directory:

```env
PROJECT_ID=your-gcp-project-id
REGION=us-central1
GITHUB_TOKEN=your_github_token
GITHUB_REPO=your_github_username/your_terraform_repo_name
DEFAULT_BRANCH=main
JIRA_URL=https://yourdomain.atlassian.net
JIRA_USER=your-email@example.com
JIRA_API_TOKEN=your-jira-api-token
GOOGLE_APPLICATION_CREDENTIALS=/path/to/your-service-account-key.json
```

---

### 5. **Install Dependencies**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Usage

1. **Create a Jira ticket** in the configured project, in the "To Do" status, describing your infrastructure change.
2. The agent will:
   - Move the ticket to **In Progress**
   - Propose and commit Terraform changes in a new branch
   - Open a PR in GitHub
   - Move the ticket to **In Review** and comment with a summary and PR link
   - Log all actions to GCP Logging
3. Review the PR in GitHub and the summary/comment in Jira.

---

## Troubleshooting

- **Permission Denied for Logging:**
  - Ensure your service account has `roles/logging.logWriter`.
  - Make sure `GOOGLE_APPLICATION_CREDENTIALS` is set and points to a valid key.
- **Jira Comment/Transition Fails:**
  - Ensure your Jira API user has permission to transition issues and add comments.
  - Double-check your Jira status names (case and whitespace sensitive).
- **ngrok Not Working:**
  - Make sure ngrok is running and you are using the HTTPS forwarding URL in Jira.
- **No PR Created:**
  - Check GCP Logging for errors.
  - Ensure the ticket is created in the "To Do" status.

---

## Notes
- The agent only processes tickets in the "To Do" status.
- All actions are logged to GCP Logging for traceability.
- You can further customize the workflow by editing `backend/main.py`.

---

For issues or contributions, please open an issue or PR on GitHub.