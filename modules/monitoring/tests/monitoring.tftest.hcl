mock_provider "google" {}

variables {
  project_id           = "northstar-test"
  gateway_service_name = "llm-security-gateway"
  alert_emails         = ["ops@example.com"]

  services = {
    "llm-security-gateway" = {
      host   = "llm-security-gateway-123456789012.us-central1.run.app"
      expect = "public"
    }
    "operations-assistant" = {
      host   = "operations-assistant-123456789012.us-central1.run.app"
      expect = "private"
    }
    "operations-performance" = {
      host   = "operations-performance-123456789012.us-central1.run.app"
      expect = "private"
    }
  }
}

run "one_uptime_check_per_service" {
  command = plan

  assert {
    condition     = length(google_monitoring_uptime_check_config.this) == 3
    error_message = "one uptime check per service expected"
  }

  assert {
    condition     = google_monitoring_uptime_check_config.this["operations-assistant"].monitored_resource[0].labels["host"] == "operations-assistant-123456789012.us-central1.run.app"
    error_message = "the check must probe the service's host"
  }
}

run "public_service_expects_2xx" {
  command = plan

  assert {
    condition     = length(google_monitoring_uptime_check_config.this["llm-security-gateway"].http_check[0].accepted_response_status_codes) == 0
    error_message = "the default (2xx) acceptance applies to the public gateway"
  }
}

run "private_services_must_answer_403" {
  command = plan

  assert {
    condition = alltrue([
      for k in ["operations-assistant", "operations-performance"] :
      one(google_monitoring_uptime_check_config.this[k].http_check[0].accepted_response_status_codes).status_value == 403
    ])
    error_message = "a private service passes only on 403 (IAM rejecting an anonymous caller); 2xx means it is no longer private"
  }
}

run "checks_use_https_with_certificate_validation" {
  command = plan

  assert {
    condition = alltrue([
      for c in google_monitoring_uptime_check_config.this :
      c.http_check[0].use_ssl == true && c.http_check[0].validate_ssl == true && c.http_check[0].port == 443
    ])
    error_message = "all checks must use validated HTTPS on 443"
  }
}

run "five_xx_alert_covers_every_service" {
  command = plan

  assert {
    condition = alltrue([
      for n in ["llm-security-gateway", "operations-assistant", "operations-performance"] :
      strcontains(google_monitoring_alert_policy.five_xx.conditions[0].condition_threshold[0].filter, "\"${n}\"")
    ])
    error_message = "the 5xx alert must include every service"
  }

  assert {
    condition     = strcontains(google_monitoring_alert_policy.five_xx.conditions[0].condition_threshold[0].filter, "response_code_class=\"5xx\"")
    error_message = "the 5xx alert must filter on the 5xx response class"
  }
}

run "block_spike_is_driven_by_the_gateway_decision_log" {
  command = plan

  assert {
    condition     = strcontains(google_logging_metric.gateway_blocks.filter, "resource.labels.service_name=\"llm-security-gateway\"") && strcontains(google_logging_metric.gateway_blocks.filter, "jsonPayload.decision=\"block\"")
    error_message = "the log metric must count decision=block on the gateway only"
  }

  assert {
    condition     = google_logging_metric.gateway_blocks.metric_descriptor[0].metric_kind == "DELTA" && google_logging_metric.gateway_blocks.metric_descriptor[0].value_type == "INT64"
    error_message = "a counter metric is a DELTA INT64"
  }

  assert {
    condition     = google_monitoring_alert_policy.gateway_block_spike.conditions[0].condition_threshold[0].threshold_value == 20
    error_message = "default block spike threshold is 20 per five minutes"
  }
}

run "thresholds_are_configurable" {
  command = plan

  variables {
    five_xx_threshold     = 2
    block_spike_threshold = 50
  }

  assert {
    condition     = google_monitoring_alert_policy.five_xx.conditions[0].condition_threshold[0].threshold_value == 2
    error_message = "five_xx_threshold must be honoured"
  }

  assert {
    condition     = google_monitoring_alert_policy.gateway_block_spike.conditions[0].condition_threshold[0].threshold_value == 50
    error_message = "block_spike_threshold must be honoured"
  }
}

run "email_channel_per_address" {
  command = plan

  variables {
    alert_emails = ["a@example.com", "b@example.com"]
  }

  assert {
    condition     = length(google_monitoring_notification_channel.email) == 2
    error_message = "one channel per address"
  }
}

run "works_without_any_email" {
  command = plan

  variables {
    alert_emails = []
  }

  assert {
    condition     = length(google_monitoring_notification_channel.email) == 0
    error_message = "no channels when no addresses are given"
  }
}

run "rejects_unsupported_period" {
  command = plan

  variables {
    uptime_period_seconds = 120
  }

  expect_failures = [var.uptime_period_seconds]
}

run "rejects_scheme_in_host" {
  command = plan

  variables {
    services = {
      bad = { host = "https://example.run.app", expect = "public" }
    }
  }

  expect_failures = [var.services]
}

run "rejects_unknown_expectation" {
  command = plan

  variables {
    services = {
      bad = { host = "example.run.app", expect = "sometimes" }
    }
  }

  expect_failures = [var.services]
}
