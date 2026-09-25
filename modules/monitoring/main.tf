locals {
  service_names = sort(keys(var.services))

  channel_ids = [for c in google_monitoring_notification_channel.email : c.id]

  runbook_link = var.runbook_url == null ? [] : [var.runbook_url]
}

resource "google_monitoring_notification_channel" "email" {
  for_each = toset(var.alert_emails)

  project      = var.project_id
  display_name = "Northstar alerts: ${each.value}"
  type         = "email"

  labels = {
    email_address = each.value
  }
}

# --- Uptime checks -----------------------------------------------------------------------------

resource "google_monitoring_uptime_check_config" "this" {
  for_each = var.services

  project      = var.project_id
  display_name = "${each.key} (${each.value.expect})"
  timeout      = "10s"
  period       = "${var.uptime_period_seconds}s"

  http_check {
    path           = each.value.path
    port           = 443
    use_ssl        = true
    validate_ssl   = true
    request_method = "GET"

    # A private service must answer 403 to an anonymous probe. Pass on 403 only: a 2xx means
    # the IAM check is no longer protecting it, and a 404/5xx means it is gone or broken.
    dynamic "accepted_response_status_codes" {
      for_each = each.value.expect == "private" ? [403] : []
      content {
        status_value = accepted_response_status_codes.value
      }
    }
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = each.value.host
    }
  }
}

resource "google_monitoring_alert_policy" "uptime" {
  project      = var.project_id
  display_name = "Northstar: uptime check failing"
  combiner     = "OR"
  severity     = "CRITICAL"
  enabled      = true

  notification_channels = local.channel_ids

  conditions {
    display_name = "Uptime check failed from more than one location"

    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.label.check_id=one_of(${join(",", [for k in local.service_names : "\"${google_monitoring_uptime_check_config.this[k].uptime_check_id}\""])})"
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "60s"

      aggregations {
        alignment_period     = "1200s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.*"]
      }

      trigger {
        count = 1
      }
    }
  }

  alert_strategy {
    auto_close = "1800s"
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "Northstar uptime check failing"
    content   = <<-EOT
      An uptime check failed from more than one probe location.

      * **Public service (gateway):** it is not answering 2xx on its health path. Check the Cloud Run revision logs.
      * **Private service:** the check passes only on HTTP 403 (the IAM invoker check rejecting an anonymous caller).
        A failure means the service is gone (404), broken (5xx) or, worse, **no longer private** (2xx). Treat a 2xx as a
        security incident: inspect `roles/run.invoker` on the service immediately.
    EOT

    dynamic "links" {
      for_each = local.runbook_link
      content {
        display_name = "runbook"
        url          = links.value
      }
    }
  }
}

# --- 5xx responses -------------------------------------------------------------------------------

resource "google_monitoring_alert_policy" "five_xx" {
  project      = var.project_id
  display_name = "Northstar: Cloud Run 5xx responses"
  combiner     = "OR"
  severity     = "ERROR"
  enabled      = true

  notification_channels = local.channel_ids

  conditions {
    display_name = "5xx responses above threshold"

    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\" AND metric.label.response_code_class=\"5xx\" AND resource.label.service_name=one_of(${join(",", [for n in local.service_names : "\"${n}\""])})"
      comparison      = "COMPARISON_GT"
      threshold_value = var.five_xx_threshold
      duration        = "0s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.service_name"]
      }

      trigger {
        count = 1
      }
    }
  }

  alert_strategy {
    auto_close = "1800s"
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "Northstar: 5xx responses"
    content   = <<-EOT
      A Cloud Run service returned more than ${var.five_xx_threshold} 5xx responses in five minutes.
      The alert is grouped per service; the incident names the one affected. Look at that service's
      revision logs. A bad deploy is the usual cause: roll back with `gcloud run services update-traffic`.
    EOT

    dynamic "links" {
      for_each = local.runbook_link
      content {
        display_name = "runbook"
        url          = links.value
      }
    }
  }
}

# --- Gateway block-rate spike --------------------------------------------------------------------

# Counts the gateway's structured decision logs. The gateway must write each decision as a JSON
# line to stdout (Cloud Run turns that into jsonPayload); its LogRecord already has the
# `decision` field ("allow" | "block") this filter keys on.
resource "google_logging_metric" "gateway_blocks" {
  project     = var.project_id
  name        = "gateway_blocks"
  description = "Requests blocked by the LLM security gateway (jsonPayload.decision = block)."
  filter      = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${var.gateway_service_name}\" AND jsonPayload.decision=\"block\""

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    unit         = "1"
    display_name = "Gateway blocked requests"
  }
}

resource "google_monitoring_alert_policy" "gateway_block_spike" {
  project      = var.project_id
  display_name = "Northstar: gateway block-rate spike"
  combiner     = "OR"
  severity     = "WARNING"
  enabled      = true

  notification_channels = local.channel_ids

  conditions {
    display_name = "Gateway blocks above threshold"

    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.gateway_blocks.name}\" AND resource.type=\"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = var.block_spike_threshold
      duration        = "0s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
      }

      trigger {
        count = 1
      }
    }
  }

  alert_strategy {
    auto_close = "1800s"
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "Northstar: gateway block-rate spike"
    content   = <<-EOT
      The LLM security gateway blocked more than ${var.block_spike_threshold} requests in five minutes.
      That is either an attack (prompt-injection probing, someone replaying a jailbreak corpus) or a
      detector regression (a bad threshold or model change blocking legitimate traffic). Compare the
      `matched_pattern_id` and `detection_layer_used` fields in the gateway's decision logs to tell which.
    EOT

    dynamic "links" {
      for_each = local.runbook_link
      content {
        display_name = "runbook"
        url          = links.value
      }
    }
  }
}
