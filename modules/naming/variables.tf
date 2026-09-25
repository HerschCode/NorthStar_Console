variable "project_id" {
  description = "Project ID. Used to derive service account emails."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be 6-30 characters of lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "project_number" {
  description = "Numeric project number. Used to derive Cloud Run's deterministic service URLs."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{6,20}$", var.project_number))
    error_message = "project_number must be the numeric project number (digits only), not the project ID."
  }
}

variable "region" {
  description = "Cloud Run region. Used to derive service URLs."
  type        = string
}
