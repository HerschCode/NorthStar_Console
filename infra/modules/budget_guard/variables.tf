variable "billing_account_id" {
  description = "Billing account that pays for the project, e.g. 0123AB-4567CD-89EF01."
  type        = string

  validation {
    condition     = can(regex("^[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}$", var.billing_account_id))
    error_message = "billing_account_id must look like 0123AB-4567CD-89EF01 (upper-case hex, three groups of six)."
  }
}

variable "project_number" {
  description = "Numeric number of the project the budget is scoped to. Spend from every other project on the billing account is ignored."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{6,20}$", var.project_number))
    error_message = "project_number must be the numeric project number (digits only), not the project ID."
  }
}

variable "display_name" {
  description = "Budget name shown in the console (max 60 characters)."
  type        = string
  default     = "northstar-dev monthly budget"

  validation {
    condition     = length(var.display_name) <= 60
    error_message = "display_name must be at most 60 characters."
  }
}

variable "currency_code" {
  description = "ISO 4217 currency of the billing account. The API rejects a budget whose currency differs from the account's."
  type        = string
  default     = "INR"

  validation {
    condition     = can(regex("^[A-Z]{3}$", var.currency_code))
    error_message = "currency_code must be a three-letter ISO 4217 code such as INR or USD."
  }
}

variable "budget_amount" {
  description = "Monthly budget in whole units of currency_code. A budget only notifies; it never stops spending."
  type        = number
  default     = 800

  validation {
    condition     = var.budget_amount > 0 && var.budget_amount == floor(var.budget_amount)
    error_message = "budget_amount must be a positive whole number."
  }
}

variable "alert_amounts" {
  description = <<-EOT
    Absolute spend levels (in currency_code) that trigger an email, ascending. They are converted to
    the percentages of budget_amount the Budget API expects: with the defaults, 100 / 400 / 800 on an
    800 budget become 12.5% / 50% / 100%.
  EOT
  type        = list(number)
  default     = [100, 400, 800]

  validation {
    condition     = length(var.alert_amounts) > 0 && alltrue([for a in var.alert_amounts : a > 0])
    error_message = "alert_amounts must be a non-empty list of positive numbers."
  }

  # Strictly ascending also implies unique. (sort() is lexicographic and string-only, so it cannot be used here.)
  validation {
    condition     = alltrue([for i in range(max(length(var.alert_amounts) - 1, 0)) : var.alert_amounts[i] < var.alert_amounts[i + 1]])
    error_message = "alert_amounts must be strictly ascending (and therefore unique)."
  }

  validation {
    condition     = alltrue([for a in var.alert_amounts : a <= 2 * var.budget_amount])
    error_message = "An alert level above twice the budget is not meaningful."
  }
}

variable "forecast_alert_percent" {
  description = "Also alert when *forecast* spend for the month crosses this fraction of the budget (1.0 = 100%). Null disables the forecast alert."
  type        = number
  default     = 1.0

  validation {
    condition     = var.forecast_alert_percent == null ? true : var.forecast_alert_percent > 0
    error_message = "forecast_alert_percent must be positive, or null to disable."
  }
}

variable "credit_types_treatment" {
  description = <<-EOT
    How credits count toward spend. EXCLUDE_ALL_CREDITS (default) alerts on gross usage, so a paid
    resource left running is noticed even while free-trial credits are still absorbing the charge.
    INCLUDE_ALL_CREDITS alerts only on what would actually be billed.
  EOT
  type        = string
  default     = "EXCLUDE_ALL_CREDITS"

  validation {
    condition     = contains(["EXCLUDE_ALL_CREDITS", "INCLUDE_ALL_CREDITS"], var.credit_types_treatment)
    error_message = "credit_types_treatment must be EXCLUDE_ALL_CREDITS or INCLUDE_ALL_CREDITS."
  }
}

variable "notification_channel_ids" {
  description = "Cloud Monitoring notification channels (projects/<id>/notificationChannels/<id>) to notify in addition to the billing admins. At most five."
  type        = list(string)
  default     = []

  validation {
    condition     = length(var.notification_channel_ids) <= 5
    error_message = "A budget supports at most five notification channels."
  }
}

variable "pubsub_topic_id" {
  description = <<-EOT
    Optional Pub/Sub topic (projects/<id>/topics/<name>) that receives every budget update as JSON.
    This is the integration point for an automated cut-off (a function that unlinks billing); no such
    function is deployed by this repository yet. Null publishes nothing.
  EOT
  type        = string
  default     = null
}
