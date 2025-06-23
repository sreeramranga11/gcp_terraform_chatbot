# GCP Terraform Chatbot Infrastructure

## Setup

1. Install Terraform: https://www.terraform.io/downloads.html
2. Authenticate with GCP:
   ```bash
   gcloud auth application-default login
   ```
3. Set your GCP project ID and region in a terraform.tfvars file:
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