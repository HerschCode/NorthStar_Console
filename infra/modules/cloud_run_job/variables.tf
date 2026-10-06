variable "project_id" {
  description = "Project that owns the job."
  type        = string
}

variable "region" {
  description = "Region to run the job in."
  type        = string
}

variable "name" {
  description = "Job name (max 49 characters: lowercase letters, digits, hyphens)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,47}[a-z0-9]$", var.name))
    error_message = "name must be 2-49 characters of lowercase letters, digits and hyphens, starting with a letter and ending with a letter or digit."
  }
}

variable "image" {
  description = "Image for the FIRST job definition only. After creation it is ignored by Terraform; the app repo's CI updates it with `gcloud run jobs deploy --image`."
  type        = string
}

variable "service_account_email" {
  description = "Runtime identity of the job. Always a dedicated service account, never the default compute account."
  type        = string

  validation {
    condition     = can(regex("\\.iam\\.gserviceaccount\\.com$", var.service_account_email)) && !can(regex("-compute@developer\\.gserviceaccount\\.com$", var.service_account_email))
    error_message = "service_account_email must be a dedicated user-managed service account (not the default compute service account)."
  }
}

variable "command" {
  description = "Entrypoint override (not run in a shell). Null keeps the image's ENTRYPOINT."
  type        = list(string)
  default     = null
}

variable "args" {
  description = "Arguments for the entrypoint. Null keeps the image's CMD."
  type        = list(string)
  default     = null
}

variable "cpu" {
  description = "CPU limit. Cloud Run's Terraform schema only accepts 1, 2, 4, 6 or 8."
  type        = string
  default     = "1"

  validation {
    condition     = contains(["1", "2", "4", "6", "8"], var.cpu)
    error_message = "cpu must be one of 1, 2, 4, 6, 8."
  }
}

variable "memory" {
  description = "Memory limit, e.g. 512Mi or 2Gi."
  type        = string
  default     = "1Gi"

  validation {
    condition     = can(regex("^[0-9]+(Mi|Gi)$", var.memory))
    error_message = "memory must look like 512Mi or 2Gi."
  }
}

variable "timeout_seconds" {
  description = "Maximum run time of one task attempt."
  type        = number
  default     = 1800

  validation {
    condition     = var.timeout_seconds >= 1 && var.timeout_seconds <= 86400
    error_message = "timeout_seconds must be between 1 and 86400."
  }
}

variable "max_retries" {
  description = "Retries per task before the execution is marked failed."
  type        = number
  default     = 1

  validation {
    condition     = var.max_retries >= 0
    error_message = "max_retries must be >= 0."
  }
}

variable "task_count" {
  description = "Number of tasks per execution."
  type        = number
  default     = 1
}

variable "env" {
  description = "Plain environment variables. Never put secrets here; use secret_env."
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for k in keys(var.env) : can(regex("^[A-Za-z_][A-Za-z0-9_]*$", k))])
    error_message = "Environment variable names must be valid identifiers."
  }

  validation {
    condition     = length(setintersection(keys(var.env), keys(var.secret_env))) == 0
    error_message = "The same variable name cannot be set in both env and secret_env."
  }
}

variable "secret_env" {
  description = "Environment variables sourced from Secret Manager: name => { secret_id, version }. The runtime service account must be the secret's accessor."
  type = map(object({
    secret_id = string
    version   = optional(string, "latest")
  }))
  default = {}

  validation {
    condition     = alltrue([for k in keys(var.secret_env) : can(regex("^[A-Za-z_][A-Za-z0-9_]*$", k))])
    error_message = "Environment variable names must be valid identifiers."
  }
}

variable "invoker_members" {
  description = "Principals allowed to start an execution (roles/run.invoker on this job only). Normally just the scheduler's service account. Public principals are never accepted."
  type        = set(string)
  default     = []

  validation {
    condition     = alltrue([for m in var.invoker_members : can(regex("^(serviceAccount|user|group):[^[:space:]]+$", m))])
    error_message = "invoker_members entries must be serviceAccount:..., user:... or group:... (never allUsers / allAuthenticatedUsers)."
  }
}

variable "deployer_members" {
  description = "Principals allowed to update the job definition (roles/run.developer on this job only)."
  type        = set(string)
  default     = []
}

variable "schedule" {
  description = "Cron expression for Cloud Scheduler. Null creates no scheduler job (run the job manually)."
  type        = string
  default     = null
}

variable "schedule_time_zone" {
  description = "Time zone the schedule is interpreted in."
  type        = string
  default     = "Etc/UTC"
}

variable "schedule_paused" {
  description = "Create the schedule paused. Keep true until a real image has been deployed, otherwise every tick runs the placeholder image and fails."
  type        = bool
  default     = true
}

variable "scheduler_region" {
  description = "Region of the Cloud Scheduler job. Null uses the job's own region."
  type        = string
  default     = null
}

variable "scheduler_service_account_email" {
  description = "Service account Cloud Scheduler uses to call the Cloud Run Admin API. Must be one of invoker_members. Required when schedule is set."
  type        = string
  default     = null
}

variable "labels" {
  description = "Labels for the job."
  type        = map(string)
  default     = {}
}

variable "deletion_protection" {
  description = "Refuse to destroy the job while true. Off by default so dev environments tear down cleanly."
  type        = bool
  default     = false
}
