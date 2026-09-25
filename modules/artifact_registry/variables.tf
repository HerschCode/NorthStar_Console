variable "project_id" {
  description = "Project that owns the repository."
  type        = string
}

variable "location" {
  description = "Repository location. Keep it in the same region as the Cloud Run services to avoid cross-region pulls."
  type        = string
}

variable "repository_id" {
  description = "Repository name, e.g. `northstar`. Images live at <location>-docker.pkg.dev/<project>/<repository_id>/<image>."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,62}$", var.repository_id))
    error_message = "repository_id must be 2-63 characters of lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "description" {
  description = "What lives in this repository."
  type        = string
  default     = "Container images for the Northstar services"
}

variable "labels" {
  description = "Labels to attach to the repository."
  type        = map(string)
  default     = {}
}

variable "keep_recent_versions" {
  description = <<-EOT
    Always keep this many of the most recent versions of each image, regardless of age. Storage
    is billed above the free 0.5 GB, so this is the main cost lever. Rolling back further than
    this needs a rebuild, because Cloud Run cannot start instances of a revision whose image is gone.
  EOT
  type        = number
  default     = 3

  validation {
    condition     = var.keep_recent_versions >= 1
    error_message = "keep_recent_versions must be at least 1."
  }
}

variable "delete_untagged_after_days" {
  description = "Delete untagged versions (orphaned layers of re-tagged builds) after this many days."
  type        = number
  default     = 7

  validation {
    condition     = var.delete_untagged_after_days >= 1
    error_message = "delete_untagged_after_days must be at least 1."
  }
}

variable "delete_older_than_days" {
  description = "Delete any version older than this many days, except the most recent `keep_recent_versions`."
  type        = number
  default     = 30

  validation {
    condition     = var.delete_older_than_days >= 1
    error_message = "delete_older_than_days must be at least 1."
  }
}

variable "immutable_tags" {
  description = "Prevent tags from being moved or deleted. Leave off if your CI re-pushes a moving tag such as `latest`."
  type        = bool
  default     = false
}

variable "writer_members" {
  description = "Principals allowed to push images (roles/artifactregistry.writer on this repository only). Typically the per-app GitHub deploy service accounts."
  type        = set(string)
  default     = []
}
