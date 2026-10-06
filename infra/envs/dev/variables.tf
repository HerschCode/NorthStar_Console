variable "project_id" {
  description = "GCP project ID (the same project the bootstrap root was applied to)."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be 6-30 characters of lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "project_number" {
  description = "Numeric project number: gcloud projects describe <project-id> --format='value(projectNumber)'. Cloud Run's deterministic URLs are built from it."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{6,20}$", var.project_number))
    error_message = "project_number must be the numeric project number (digits only), not the project ID."
  }
}

variable "region" {
  description = "Region for Cloud Run, Artifact Registry, Secret Manager replicas and Cloud Scheduler. us-central1 is a free-tier region."
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment label."
  type        = string
  default     = "dev"
}

# --- Neon Postgres (external; only the non-secret connection details live here) --------------

variable "neon_host" {
  description = "Hostname of the Neon Postgres endpoint. Not a secret; the two role passwords are, and live in Secret Manager."
  type        = string
}

variable "neon_database" {
  description = "Database name."
  type        = string
  default     = "operations_performance"
}

variable "neon_api_user" {
  description = "Read-only database role used by the API (ops_api_reader in operations-performance's sql/schema/004_create_roles.sql)."
  type        = string
  default     = "ops_api_reader"
}

variable "neon_pipeline_user" {
  description = "Read/write database role used by the pipeline job (ops_pipeline_writer)."
  type        = string
  default     = "ops_pipeline_writer"
}

# --- Images -------------------------------------------------------------------------------------

variable "placeholder_image" {
  description = "Image the three services start with until each app repo's CI ships a real one. Terraform ignores the image afterwards."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "placeholder_job_image" {
  description = "Image the pipeline job starts with. A real one replaces it on the first deploy from operations-performance's CI."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/job"
}

variable "use_http_startup_probes" {
  description = "Probe GET /health on startup instead of Cloud Run's default TCP check. Leave false until real images are deployed: the placeholder image has no /health."
  type        = bool
  default     = false
}

# --- Sizing (scale to zero, small ceilings: the free tier and the budget are the constraints) --

variable "gateway_memory" {
  description = "Gateway memory. It serves three detection layers (rules, TF-IDF similarity and a numpy classifier), no torch."
  type        = string
  default     = "1Gi"
}

variable "assistant_memory" {
  description = "Assistant memory (Chroma vector store and embeddings)."
  type        = string
  default     = "2Gi"
}

variable "performance_memory" {
  description = "Performance API memory."
  type        = string
  default     = "1Gi"
}

variable "pipeline_memory" {
  description = "Pipeline job memory."
  type        = string
  default     = "1Gi"
}

variable "max_instances" {
  description = "Maximum instances per service. Also the ceiling on what a traffic flood can cost."
  type        = number
  default     = 2
}

# --- Assistant configuration ----------------------------------------------------------------------

variable "agent_provider" {
  description = "LLM provider the assistant uses (anthropic, groq or gemini). Must match a key in llm_secrets."
  type        = string
  default     = "groq"

  validation {
    condition     = contains(["anthropic", "groq", "gemini"], var.agent_provider)
    error_message = "agent_provider must be anthropic, groq or gemini."
  }
}

variable "agent_model" {
  description = "Model name passed to the provider."
  type        = string
  default     = "openai/gpt-oss-120b"
}

variable "llm_secrets" {
  description = <<-EOT
    LLM API keys the assistant may use: Secret Manager secret ID => environment variable it is
    exposed as. Each secret is readable only by the assistant's service account. Terraform creates
    the empty container plus a placeholder version; add the real key with
    `gcloud secrets versions add <id> --data-file=-` so it never enters Terraform state.
  EOT
  type        = map(string)
  default = {
    "groq-api-key" = "GROQ_API_KEY"
  }
}

# --- Pipeline schedule ----------------------------------------------------------------------------

variable "pipeline_command" {
  description = "Entrypoint of the pipeline job (the image's own ENTRYPOINT is empty)."
  type        = list(string)
  default     = ["python"]
}

variable "pipeline_args" {
  description = "Arguments of the pipeline job. To also load BigQuery, chain the migration in the image or add a second job."
  type        = list(string)
  default     = ["-m", "scripts.run_pipeline"]
}

variable "pipeline_schedule" {
  description = "Cron schedule for the pipeline job."
  type        = string
  default     = "0 2 * * *"
}

variable "pipeline_schedule_paused" {
  description = "Create the schedule paused. Unpause once a real pipeline image has been deployed."
  type        = bool
  default     = true
}

# --- Data -------------------------------------------------------------------------------------------

variable "bigquery_location" {
  description = "BigQuery dataset location. The US multi-region is fine next to us-central1."
  type        = string
  default     = "US"
}

# --- Observability ----------------------------------------------------------------------------------

variable "alert_emails" {
  description = "Addresses that receive uptime, 5xx and block-rate alerts."
  type        = list(string)
  default     = []
}

variable "uptime_period_seconds" {
  description = "How often each uptime check runs (60, 300, 600 or 900)."
  type        = number
  default     = 300
}

variable "five_xx_threshold" {
  description = "5xx responses within five minutes that trigger an alert."
  type        = number
  default     = 5
}

variable "block_spike_threshold" {
  description = "Gateway blocks within five minutes that trigger an alert."
  type        = number
  default     = 20
}

variable "runbook_url" {
  description = "Link included in every alert. Null omits it."
  type        = string
  default     = null
}
