variable "project_id" {
  description = "Project that hosts the workload identity pool."
  type        = string
}

variable "project_number" {
  description = "Numeric project number (gcloud projects describe <id> --format='value(projectNumber)'). Used to build principal strings that are known at plan time."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{6,20}$", var.project_number))
    error_message = "project_number must be the numeric project number (digits only), not the project ID."
  }
}

variable "pool_id" {
  description = "Workload identity pool ID (4-32 characters of a-z, 0-9, hyphen; the gcp- prefix is reserved)."
  type        = string
  default     = "github"

  validation {
    condition     = can(regex("^[a-z0-9-]{4,32}$", var.pool_id)) && !startswith(var.pool_id, "gcp-")
    error_message = "pool_id must be 4-32 characters of a-z, 0-9 and hyphen, and must not start with gcp-."
  }
}

variable "provider_id" {
  description = "Workload identity pool provider ID (4-32 characters of a-z, 0-9, hyphen; the gcp- prefix is reserved)."
  type        = string
  default     = "github-oidc"

  validation {
    condition     = can(regex("^[a-z0-9-]{4,32}$", var.provider_id)) && !startswith(var.provider_id, "gcp-")
    error_message = "provider_id must be 4-32 characters of a-z, 0-9 and hyphen, and must not start with gcp-."
  }
}

variable "github_owner" {
  description = "GitHub user or organisation that owns every repository allowed to federate. Tokens from any other owner are rejected by the provider itself."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9-]{1,39}$", var.github_owner))
    error_message = "github_owner must be a valid GitHub user or organisation name."
  }
}

variable "github_repositories" {
  description = "Every repository (OWNER/REPO) allowed to exchange a GitHub OIDC token at all. Anything not listed is rejected before any service-account binding is even consulted."
  type        = set(string)

  validation {
    condition     = length(var.github_repositories) > 0
    error_message = "List at least one repository."
  }

  validation {
    condition     = alltrue([for r in var.github_repositories : can(regex("^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$", r))])
    error_message = "Each repository must look like OWNER/REPO."
  }

  validation {
    condition     = alltrue([for r in var.github_repositories : startswith(r, "${var.github_owner}/")])
    error_message = "Every repository must belong to github_owner."
  }
}

variable "impersonation" {
  description = <<-EOT
    Which GitHub workflows may impersonate which service account. Keyed by an arbitrary label.
    `service_account_name` is the fully-qualified name (projects/<project>/serviceAccounts/<email>).
    Each `allowed` entry pins the grant to one repository AND either a git ref (for example
    refs/heads/main) or a GitHub environment, so a token from another branch, a pull request or
    another environment cannot use it. Set at most one of `ref` and `environment` per entry.
    The grant is authoritative for roles/iam.workloadIdentityUser on that service account.
  EOT
  type = map(object({
    service_account_name = string
    allowed = list(object({
      repository  = string
      ref         = optional(string)
      environment = optional(string)
    }))
  }))
  default = {}

  validation {
    condition = alltrue([
      for k, v in var.impersonation : alltrue([for a in v.allowed : contains(var.github_repositories, a.repository)])
    ])
    error_message = "Every impersonation entry must use a repository that is listed in github_repositories."
  }

  validation {
    condition = alltrue([
      for k, v in var.impersonation : alltrue([for a in v.allowed : !(a.ref != null && a.environment != null)])
    ])
    error_message = "Set at most one of ref and environment per allowed entry (a principal can match only one attribute)."
  }

  validation {
    condition = alltrue([
      for k, v in var.impersonation : alltrue([for a in v.allowed : a.ref == null ? true : startswith(a.ref, "refs/")])
    ])
    error_message = "ref must be a full git ref such as refs/heads/main."
  }

  validation {
    condition     = alltrue([for k, v in var.impersonation : length(v.allowed) > 0])
    error_message = "Every impersonation entry needs at least one allowed workflow."
  }
}
