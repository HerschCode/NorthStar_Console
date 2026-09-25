variable "project_id" {
  description = "Project that owns the secret."
  type        = string
}

variable "secret_id" {
  description = "Secret ID, unique within the project (letters, digits, hyphens, underscores)."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,255}$", var.secret_id))
    error_message = "secret_id may contain only letters, digits, hyphens and underscores (max 255 characters)."
  }
}

variable "accessor_service_account_email" {
  description = <<-EOT
    Email of the ONE service account allowed to read this secret's payload. The module takes a
    single value on purpose: if two workloads need the same value, create two secrets. That keeps
    every secret readable by exactly one identity, which the conftest policy also enforces.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\\.iam\\.gserviceaccount\\.com$", var.accessor_service_account_email))
    error_message = "accessor_service_account_email must be a user-managed service account email (name@project.iam.gserviceaccount.com)."
  }
}

variable "replica_locations" {
  description = <<-EOT
    Regions to replicate the secret to. Empty means automatic replication. Secret Manager bills
    per active version per location, so a single region is both cheaper and keeps the data in
    one place.
  EOT
  type        = list(string)
  default     = []
}

variable "labels" {
  description = "Labels to attach to the secret."
  type        = map(string)
  default     = {}
}

variable "create_placeholder_version" {
  description = <<-EOT
    Create version 1 with an obvious placeholder so services that reference `latest` can start on
    the first apply. It is written through a write-only argument, so it is never stored in state or
    plan output. Add the real value out of band (gcloud secrets versions add); Terraform never sees it.
  EOT
  type        = bool
  default     = true
}

variable "deletion_protection" {
  description = "Refuse to destroy the secret while true. Off by default so dev environments tear down cleanly."
  type        = bool
  default     = false
}
