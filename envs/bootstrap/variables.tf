variable "project_id" {
  description = "GCP project ID. The project and its billing link are created by hand before the first apply (see docs/runbook.md)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be 6-30 characters of lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "project_number" {
  description = "Numeric project number: gcloud projects describe <project-id> --format='value(projectNumber)'."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{6,20}$", var.project_number))
    error_message = "project_number must be the numeric project number (digits only), not the project ID."
  }
}

variable "region" {
  description = "Default region. us-central1 is a free-tier region for Cloud Run, Artifact Registry and Cloud Storage."
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment label."
  type        = string
  default     = "dev"
}

variable "billing_account_id" {
  description = "Billing account that pays for the project (gcloud billing accounts list)."
  type        = string

  validation {
    condition     = can(regex("^[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}$", var.billing_account_id))
    error_message = "billing_account_id must look like 0123AB-4567CD-89EF01."
  }
}

variable "currency_code" {
  description = "Currency of the billing account. The Budget API rejects a budget in any other currency."
  type        = string
  default     = "INR"
}

variable "budget_amount" {
  description = "Monthly budget in whole units of currency_code."
  type        = number
  default     = 800
}

variable "alert_amounts" {
  description = "Spend levels that trigger an email, ascending, in currency_code."
  type        = list(number)
  default     = [100, 400, 800]
}

variable "github_owner" {
  description = "GitHub user or organisation that owns the repositories. Tokens from any other owner are rejected."
  type        = string
}

variable "github_repos" {
  description = "Repository names (without the owner) of the infrastructure repo and the three apps."
  type = object({
    infra       = string
    gateway     = string
    assistant   = string
    performance = string
  })
  default = {
    infra       = "northstar-infra"
    gateway     = "llm-security-gateway"
    assistant   = "operations-assistant"
    performance = "operations-performance"
  }
}

variable "deploy_ref" {
  description = "Git ref the app deploy identities are pinned to. Only workflows running on this ref can deploy."
  type        = string
  default     = "refs/heads/main"
}

variable "terraform_apply_environment" {
  description = "GitHub environment that gates `terraform apply`. Configure it with required reviewers and a main-only deployment branch rule; the apply identity can only be impersonated by jobs running in it."
  type        = string
  default     = "dev-apply"
}

variable "dev_state_bucket" {
  description = "Name of the GCS bucket holding the dev root's state (created by hand, see docs/runbook.md). The plan identity gets read access and the apply identity read/write. It is a different bucket from the one holding this root's state."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$", var.dev_state_bucket))
    error_message = "dev_state_bucket must be a valid bucket name."
  }
}
