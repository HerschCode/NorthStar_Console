variable "project_id" {
  description = "Project that owns the service account."
  type        = string
}

variable "account_id" {
  description = "Service account ID (the part before the @): 6-30 characters, lowercase letters, digits and hyphens."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.account_id))
    error_message = "account_id must be 6-30 characters of lowercase letters, digits and hyphens, starting with a letter and ending with a letter or digit."
  }
}

variable "display_name" {
  description = "Human-readable name shown in the console."
  type        = string
}

variable "description" {
  description = "What this identity is for and what it may touch (max 256 bytes)."
  type        = string
  default     = ""
}

variable "project_roles" {
  description = <<-EOT
    Roles granted on the whole project. Only for permissions GCP defines at project scope
    (for example bigquery.jobs.create). Anything that can be granted on a single resource
    (secret, dataset, Cloud Run service, repository) must be granted on that resource instead.
    Primitive roles (owner, editor, viewer) are rejected.
  EOT
  type        = set(string)
  default     = []

  validation {
    condition     = length(setintersection(var.project_roles, ["roles/owner", "roles/editor", "roles/viewer"])) == 0
    error_message = "Primitive roles (roles/owner, roles/editor, roles/viewer) are not allowed. Grant a predefined or custom role on the narrowest resource possible."
  }
}

variable "service_account_user_members" {
  description = <<-EOT
    Principals allowed to attach or act as this service account (roles/iam.serviceAccountUser
    on this service account only, never on the project). Deployers need this to create
    Cloud Run revisions that run as it.
  EOT
  type        = set(string)
  default     = []
}
