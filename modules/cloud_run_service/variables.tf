variable "project_id" {
  description = "Project that owns the service."
  type        = string
}

variable "region" {
  description = "Region to deploy to."
  type        = string
}

variable "name" {
  description = "Service name (max 49 characters: lowercase letters, digits, hyphens)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,47}[a-z0-9]$", var.name))
    error_message = "name must be 2-49 characters of lowercase letters, digits and hyphens, starting with a letter and ending with a letter or digit."
  }
}

variable "description" {
  description = "What the service does."
  type        = string
  default     = ""
}

variable "image" {
  description = <<-EOT
    Image for the FIRST revision only (a public hello-world placeholder until the app repo's CI
    ships a real one). After creation the image is ignored by Terraform (lifecycle.ignore_changes),
    so app deployments never fight infrastructure applies. Config (env, secrets, scaling, identity)
    stays owned by Terraform; app CI must deploy with `--image` only.
  EOT
  type        = string
}

variable "service_account_email" {
  description = "Runtime identity. Always a dedicated service account, never the default compute account."
  type        = string

  validation {
    condition     = can(regex("\\.iam\\.gserviceaccount\\.com$", var.service_account_email)) && !can(regex("-compute@developer\\.gserviceaccount\\.com$", var.service_account_email))
    error_message = "service_account_email must be a dedicated user-managed service account (not the default compute service account)."
  }
}

variable "container_port" {
  description = "Port the container listens on. Cloud Run also injects it as $PORT; all three apps already honour that."
  type        = number
  default     = 8080
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
  default     = "512Mi"

  validation {
    condition     = can(regex("^[0-9]+(Mi|Gi)$", var.memory))
    error_message = "memory must look like 512Mi or 2Gi."
  }
}

variable "min_instances" {
  description = "Minimum instances. Keep at 0 (scale to zero) to stay inside the free tier."
  type        = number
  default     = 0

  validation {
    condition     = var.min_instances >= 0
    error_message = "min_instances must be >= 0."
  }
}

variable "max_instances" {
  description = "Maximum instances. Also the ceiling on cost if something floods the service."
  type        = number
  default     = 2

  validation {
    condition     = var.max_instances >= 1
    error_message = "max_instances must be >= 1."
  }
}

variable "max_concurrency" {
  description = "Concurrent requests per instance."
  type        = number
  default     = 80
}

variable "timeout_seconds" {
  description = "Per-request timeout (1-3600). The gateway waits on an LLM-backed agent, so it needs more than the 300 s default of some stacks."
  type        = number
  default     = 300

  validation {
    condition     = var.timeout_seconds >= 1 && var.timeout_seconds <= 3600
    error_message = "timeout_seconds must be between 1 and 3600."
  }
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
    condition     = !contains(keys(var.env), "PORT")
    error_message = "PORT is reserved: Cloud Run injects it. Use container_port instead."
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

variable "ingress" {
  description = <<-EOT
    Ingress setting. INGRESS_TRAFFIC_ALL is required here because there is no load balancer or VPC
    egress path (both cost money); the private services are still protected by the IAM invoker check,
    which answers 403 to any caller without a valid token from an allowed principal.
  EOT
  type        = string
  default     = "INGRESS_TRAFFIC_ALL"

  validation {
    condition     = contains(["INGRESS_TRAFFIC_ALL", "INGRESS_TRAFFIC_INTERNAL_ONLY", "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"], var.ingress)
    error_message = "ingress must be INGRESS_TRAFFIC_ALL, INGRESS_TRAFFIC_INTERNAL_ONLY or INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER."
  }
}

variable "invoker_members" {
  description = <<-EOT
    Principals allowed to call the service (roles/run.invoker). Authoritative: anyone added out of
    band is removed on the next apply. `allUsers` is only accepted together with allow_public = true.
    `allAuthenticatedUsers` is never accepted (any Google account is effectively public).
  EOT
  type        = set(string)

  validation {
    condition     = length(var.invoker_members) > 0
    error_message = "invoker_members must not be empty: a service nobody may call is a misconfiguration."
  }

  validation {
    condition     = alltrue([for m in var.invoker_members : can(regex("^(allUsers|(serviceAccount|user|group):[^[:space:]]+)$", m))])
    error_message = "invoker_members entries must be allUsers, serviceAccount:..., user:... or group:... (allAuthenticatedUsers and domain: are not accepted)."
  }
}

variable "allow_public" {
  description = "Second key for public exposure: must be true for `allUsers` to be accepted in invoker_members. Only the gateway sets this."
  type        = bool
  default     = false
}

variable "deployer_members" {
  description = "Principals allowed to deploy new revisions (roles/run.developer on this service only)."
  type        = set(string)
  default     = []
}

variable "startup_probe_path" {
  description = "HTTP path for the startup probe. Null keeps Cloud Run's default TCP probe (needed while the placeholder image, which has no /health, is deployed)."
  type        = string
  default     = null
}

variable "labels" {
  description = "Labels for the service."
  type        = map(string)
  default     = {}
}

variable "deletion_protection" {
  description = "Refuse to destroy the service while true. Off by default so dev environments tear down cleanly."
  type        = bool
  default     = false
}
