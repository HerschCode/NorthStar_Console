mock_provider "google" {}

variables {
  billing_account_id = "0123AB-4567CD-89EF01"
  project_number     = "123456789012"
}

run "rupee_levels_become_fractions_of_the_budget" {
  command = plan

  assert {
    condition     = output.thresholds["100"] == 0.125 && output.thresholds["400"] == 0.5 && output.thresholds["800"] == 1
    error_message = "100/400/800 on an 800 budget must be 12.5% / 50% / 100%"
  }

  assert {
    condition = [
      for r in google_billing_budget.this.threshold_rules : r.threshold_percent if r.spend_basis == "CURRENT_SPEND"
    ] == [0.125, 0.5, 1]
    error_message = "the API must receive the converted fractions, in order"
  }
}

run "forecast_alert_is_on_by_default" {
  command = plan

  assert {
    condition     = length([for r in google_billing_budget.this.threshold_rules : r if r.spend_basis == "FORECASTED_SPEND"]) == 1
    error_message = "one forecast rule expected"
  }
}

run "forecast_alert_can_be_disabled" {
  command = plan

  variables {
    forecast_alert_percent = null
  }

  assert {
    condition     = length([for r in google_billing_budget.this.threshold_rules : r if r.spend_basis == "FORECASTED_SPEND"]) == 0
    error_message = "no forecast rule when disabled"
  }
}

run "budget_is_scoped_to_this_project_in_rupees" {
  command = plan

  assert {
    condition     = one(google_billing_budget.this.budget_filter[0].projects) == "projects/123456789012"
    error_message = "the budget must only watch this project"
  }

  assert {
    condition     = google_billing_budget.this.amount[0].specified_amount[0].currency_code == "INR" && google_billing_budget.this.amount[0].specified_amount[0].units == "800"
    error_message = "800 INR expected"
  }

  assert {
    condition     = google_billing_budget.this.budget_filter[0].calendar_period == "MONTH"
    error_message = "monthly budget expected"
  }
}

run "credits_are_excluded_by_default" {
  command = plan

  assert {
    condition     = google_billing_budget.this.budget_filter[0].credit_types_treatment == "EXCLUDE_ALL_CREDITS"
    error_message = "gross usage must be watched by default"
  }
}

run "default_recipients_only_when_nothing_extra_is_configured" {
  command = plan

  assert {
    condition     = length(google_billing_budget.this.all_updates_rule) == 0
    error_message = "no all_updates_rule means billing admins are emailed on thresholds and no per-update feed is sent"
  }
}

run "pubsub_topic_adds_the_notification_feed" {
  command = plan

  variables {
    pubsub_topic_id = "projects/northstar-test/topics/budget-alerts"
  }

  assert {
    condition     = google_billing_budget.this.all_updates_rule[0].pubsub_topic == "projects/northstar-test/topics/budget-alerts" && google_billing_budget.this.all_updates_rule[0].schema_version == "1.0"
    error_message = "topic and schema version 1.0 expected"
  }
}

run "rejects_unsorted_alert_levels" {
  command = plan

  variables {
    alert_amounts = [400, 100, 800]
  }

  expect_failures = [var.alert_amounts]
}

run "rejects_alert_level_far_above_the_budget" {
  command = plan

  variables {
    alert_amounts = [100, 5000]
  }

  expect_failures = [var.alert_amounts]
}

run "rejects_fractional_budget" {
  command = plan

  variables {
    budget_amount = 800.5
  }

  expect_failures = [var.budget_amount]
}

run "rejects_malformed_billing_account" {
  command = plan

  variables {
    billing_account_id = "not-an-account"
  }

  expect_failures = [var.billing_account_id]
}

run "rejects_more_than_five_channels" {
  command = plan

  variables {
    notification_channel_ids = ["a", "b", "c", "d", "e", "f"]
  }

  expect_failures = [var.notification_channel_ids]
}
