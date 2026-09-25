variable "project_id" {
  description = "Project that owns the dataset."
  type        = string
}

variable "dataset_id" {
  description = "Dataset ID: letters, digits and underscores."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_]+$", var.dataset_id)) && length(var.dataset_id) <= 1024
    error_message = "dataset_id may contain only letters, digits and underscores (max 1024 characters)."
  }
}

variable "location" {
  description = "Dataset location. Cannot be changed later (it forces a new dataset)."
  type        = string
  default     = "US"
}

variable "description" {
  description = "What the dataset holds."
  type        = string
  default     = ""
}

variable "labels" {
  description = "Labels for the dataset."
  type        = map(string)
  default     = {}
}

variable "delete_contents_on_destroy" {
  description = "Delete all tables when the dataset is destroyed. Needed for a clean `terraform destroy` of a dev environment; keep false anywhere the data matters."
  type        = bool
  default     = false
}

variable "default_table_expiration_ms" {
  description = "Default lifetime of new tables in milliseconds (min 3600000). Null means tables never expire."
  type        = number
  default     = null
}

variable "deletion_protection" {
  description = "Refuse to destroy tables while true."
  type        = bool
  default     = true
}

variable "tables" {
  description = <<-EOT
    Tables to create, keyed by table ID. `schema` is a BigQuery JSON schema string. Partitioning
    and clustering are what keep queries cheap (BigQuery bills by bytes scanned), so a table
    with a time column should declare both. Terraform creates the empty, correctly partitioned
    shells first; loaders must then append to them rather than recreate them.
  EOT
  type = map(object({
    description = optional(string, "")
    schema      = string
    time_partitioning = optional(object({
      type          = optional(string, "DAY")
      field         = string
      expiration_ms = optional(number)
    }))
    clustering               = optional(list(string), [])
    require_partition_filter = optional(bool, false)
  }))
  default = {}

  validation {
    condition     = alltrue([for t in values(var.tables) : can(jsondecode(t.schema))])
    error_message = "Every table schema must be valid JSON."
  }

  validation {
    condition = alltrue([
      for t in values(var.tables) :
      try(t.time_partitioning == null || contains([for f in jsondecode(t.schema) : f.name], t.time_partitioning.field), false)
    ])
    error_message = "time_partitioning.field must be a column of the table's schema."
  }

  validation {
    condition = alltrue([
      for t in values(var.tables) :
      try(alltrue([for c in t.clustering : contains([for f in jsondecode(t.schema) : f.name], c)]), false)
    ])
    error_message = "Every clustering column must be a column of the table's schema."
  }

  validation {
    condition     = alltrue([for t in values(var.tables) : length(t.clustering) <= 4])
    error_message = "BigQuery allows at most 4 clustering columns."
  }
}

variable "viewer_members" {
  description = "Principals that may read the dataset's tables (roles/bigquery.dataViewer on this dataset only). Authoritative for that role. Running queries additionally needs roles/bigquery.jobUser on the project."
  type        = set(string)
  default     = []
}

variable "editor_members" {
  description = "Principals that may read and write the dataset's tables (roles/bigquery.dataEditor on this dataset only). Authoritative for that role."
  type        = set(string)
  default     = []
}
