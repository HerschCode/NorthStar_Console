locals {
  # The Budget API wants fractions of the budget (0.5 = 50%), not absolute amounts.
  thresholds = [for a in var.alert_amounts : a / var.budget_amount]

  notify_extra = length(var.notification_channel_ids) > 0 || var.pubsub_topic_id != null
}

resource "google_billing_budget" "this" {
  billing_account = var.billing_account_id
  display_name    = var.display_name

  budget_filter {
    projects               = ["projects/${var.project_number}"]
    credit_types_treatment = var.credit_types_treatment
    calendar_period        = "MONTH"
  }

  amount {
    specified_amount {
      currency_code = var.currency_code
      units         = tostring(var.budget_amount)
    }
  }

  dynamic "threshold_rules" {
    for_each = local.thresholds
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }

  dynamic "threshold_rules" {
    for_each = var.forecast_alert_percent == null ? [] : [var.forecast_alert_percent]
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "FORECASTED_SPEND"
    }
  }

  # With no all_updates_rule the billing admins are emailed when a threshold is crossed, which is
  # what we want by default. Adding one is only needed for extra channels or a Pub/Sub topic.
  dynamic "all_updates_rule" {
    for_each = local.notify_extra ? [1] : []
    content {
      monitoring_notification_channels = var.notification_channel_ids
      pubsub_topic                     = var.pubsub_topic_id
      schema_version                   = var.pubsub_topic_id == null ? null : "1.0"
    }
  }
}
