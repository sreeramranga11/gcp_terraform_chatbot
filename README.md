# GCP Terraform Chatbot

## Overview

GCP Terraform Chatbot is a conversational assistant that manages GCP infrastructure using plain-English commands. It translates user requests into Terraform changes, integrates with GitHub for version control and approvals, and applies infrastructure updates safely and transparently.

## Architecture

- **Backend**: FastAPI app that handles chat, integrates with Vertex AI for code generation, manages GitHub PRs, and applies Terraform changes using a robust block-level replacement algorithm. **The backend fetches Terraform code from the repository specified in the `.env` file (`GITHUB_REPO`).**
- **Frontend**: React chat interface for user interaction, review, and approval of proposed infrastructure changes.
- **Infrastructure**: Terraform scripts for GCP resources, managed in a separate repository (as specified by `GITHUB_REPO`).

## How It Works

1. **User Interaction**: Users describe infrastructure changes in natural language via the chat UI.
2. **Context Fetching**: The backend fetches the latest Terraform files from the repo specified in `.env` (`GITHUB_REPO`) and provides them as context to the model.
3. **Model Generation**: Vertex AI generates updated Terraform blocks for only the resources/modules that need to change, outputting them in a structured format.
4. **Block Replacement (Bracket Counting)**: The backend parses the model output, finds the corresponding block in the Terraform file using a bracket-counting algorithm (not regex), and replaces it. This ensures correct handling of nested blocks and avoids extra/missing braces.
5. **Approval Workflow**: The frontend displays the proposed changes. Users can approve or reject them.
6. **GitHub Integration**: On approval, the backend creates a new branch, commits the changes, and opens a pull request for review and merge.

### Why Bracket Counting?
- **Robustness**: Handles nested blocks and any Terraform formatting, avoiding the pitfalls of regex-based matching.
- **Safety**: Prevents malformed files due to extra or missing braces.
- **Precision**: Only the intended block is replaced, leaving the rest of the file untouched.

## Setup

### 1. Infrastructure (Terraform)

1. Install [Terraform](https://www.terraform.io/downloads.html)
2. Authenticate with GCP:
   ```bash
   gcloud auth application-default login
   ```
3. Set your GCP project ID and region in a `terraform.tfvars` file:
   ```hcl
   project_id = "your-gcp-project-id"
   region    = "us-central1"
   ```
4. Initialize and apply Terraform:
   ```bash
   terraform init
   terraform apply
   ```
   This will enable required APIs and create a service account for the chatbot.

### 2. Backend (FastAPI)

1. Create a virtual environment and activate it:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Create a `.env` file in the backend directory with:
   ```env
   PROJECT_ID=gcp-terraform-chatbot
   REGION=us-central1
   GITHUB_TOKEN=your_github_token
   GITHUB_REPO=your_github_username/your_terraform_repo_name  # <-- This is the repo containing your Terraform code
   DEFAULT_BRANCH=main
   ```
   Also set the service account key path in your shell:
   ```bash
   export GOOGLE_APPLICATION_CREDENTIALS="/path/to/your-service-account-key.json"
   ```
   **Note:** The backend will fetch and update Terraform files from the repository specified by `GITHUB_REPO` in your `.env` file.
4. Run the FastAPI server:
   ```bash
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```
   The backend will be available at http://localhost:8000.

### 3. Frontend (React)

1. Install dependencies:
   ```bash
   npm install
   ```
2. Start the development server:
   ```bash
   npm start
   ```
   The app will run on http://localhost:3000 and expects the backend to be running on http://localhost:8000.

## Usage

1. Open the frontend in your browser.
2. Enter a plain-English request for infrastructure changes (e.g., "Increase the GKE node pool size to 5").
3. Review the proposed Terraform block changes in the chat UI.
4. Approve or reject the changes.
5. On approval, a pull request is created in your GitHub repo for review and merge.

## Technical Highlights

- **Block-level patching**: Only the affected Terraform blocks are replaced, not the whole file.
- **Bracket-counting algorithm**: Ensures correct block boundaries, even with nested or complex blocks.
- **Multi-file, multi-block support**: Handles changes across multiple files and resources in a single workflow.
- **Safe fallback**: If a block is not found, it is appended at the end of the file (with a debug log).
- **Full audit trail**: All changes go through GitHub PRs for review and traceability.

## Why This Approach Is Good
- **Minimizes risk**: Only the intended changes are made, reducing the chance of breaking infrastructure.
- **User-friendly**: Natural language interface and clear approval workflow.
- **Maintainable**: The bracket-counting logic is robust to Terraform formatting and future changes.
- **Extensible**: Easy to add support for new resource types or workflows.

---

For any issues or contributions, please open an issue or PR on GitHub.