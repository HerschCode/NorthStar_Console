variable "project_id" {
  description = "Project that owns the monitoring resources."
  type        = string
}

variable "alert_emails" {
  description = "Email addresses that receive alerts. Empty creates the policies without a notification channel (they still open incidents in the console)."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for e in var.alert_emails : can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", e))])
    error_message = "alert_emails must be valid email addresses."
  }
}

variable "services" {
  description = <<-EOT
    One uptime check per Cloud Run service, keyed by service name. `host` is the bare hostname
    (no scheme). `expect` says what a healthy service looks like from the public internet:
      - public:  answers 2xx on `path` (the gateway, the only service anyone may call).
      - private: answers 403, because the IAM invoker check rejects an unauthenticated caller.
                 The check passes only on 403, so it proves the service is up AND still private:
                 if a private service is ever opened to allUsers the check flips to failing.
  EOT
  type = map(object({
    host   = string
    path   = optional(string, "/health")
    expect = string
  }))

  validation {
    condition     = alltrue([for s in values(var.services) : contains(["public", "private"], s.expect)])
    error_message = "expect must be \"public\" or \"private\"."
  }

  validation {
    condition     = alltrue([for s in values(var.services) : !can(regex("^https?://", s.host))])
    error_message = "host must be a bare hostname without a scheme."
  }
}

variable "uptime_period_seconds" {
  description = "How often each uptime check runs. The free allowance is 1 million executions a month; 300 s across all probe regions and three services stays far below it."
  type        = number
  default     = 300

  validation {
    condition     = contains([60, 300, 600, 900], var.uptime_period_seconds)
    error_message = "uptime_period_seconds must be 60, 300, 600 or 900."
  }
}

variable "gateway_service_name" {
  description = "Cloud Run service whose structured logs are counted for the block-rate alert."
  type        = string
}

variable "five_xx_threshold" {
  description = "Alert when a service returns more than this many 5xx responses within five minutes."
  type        = number
  default     = 5
}

variable "block_spike_threshold" {
  description = "Alert when the gateway blocks more than this many requests within five minutes."
  type        = number
  default     = 20
}

variable "runbook_url" {
  description = "Link included in every alert's documentation. Null omits it."
  type        = string
  default     = null
}
