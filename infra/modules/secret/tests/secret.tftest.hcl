mock_provider "google" {}

variables {
  project_id                     = "northstar-test"
  secret_id                      = "groq-api-key"
  accessor_service_account_email = "assistant-sa@northstar-test.iam.gserviceaccount.com"
}

run "exactly_one_reader" {
  command = plan

  assert {
    condition     = length(google_secret_manager_secret_iam_binding.accessor.members) == 1
    error_message = "a secret must be readable by exactly one principal"
  }

  assert {
    condition     = one(google_secret_manager_secret_iam_binding.accessor.members) == "serviceAccount:assistant-sa@northstar-test.iam.gserviceaccount.com"
    error_message = "the reader must be the requested service account"
  }

  assert {
    condition     = google_secret_manager_secret_iam_binding.accessor.role == "roles/secretmanager.secretAccessor"
    error_message = "only secretAccessor may be granted"
  }
}

run "binding_is_keyed_by_a_plan_time_secret_id" {
  command = plan

  assert {
    condition     = google_secret_manager_secret_iam_binding.accessor.secret_id == "groq-api-key"
    error_message = "secret_id on the binding must be the configured short ID (known at plan time)"
  }
}

run "placeholder_version_by_default" {
  command = plan

  assert {
    condition     = length(google_secret_manager_secret_version.placeholder) == 1
    error_message = "placeholder version expected by default"
  }
}

run "placeholder_can_be_disabled" {
  command = plan

  variables {
    create_placeholder_version = false
  }

  assert {
    condition     = length(google_secret_manager_secret_version.placeholder) == 0
    error_message = "no placeholder when disabled"
  }
}

run "automatic_replication_by_default" {
  command = plan

  assert {
    condition     = length(google_secret_manager_secret.this.replication[0].auto) == 1 && length(google_secret_manager_secret.this.replication[0].user_managed) == 0
    error_message = "automatic replication expected when no locations are given"
  }
}

run "single_region_replication" {
  command = plan

  variables {
    replica_locations = ["us-central1"]
  }

  assert {
    condition     = length(google_secret_manager_secret.this.replication[0].user_managed) == 1
    error_message = "user-managed replication expected when locations are given"
  }
}

run "rejects_human_accessor" {
  command = plan

  variables {
    accessor_service_account_email = "someone@gmail.com"
  }

  expect_failures = [var.accessor_service_account_email]
}

run "rejects_bad_secret_id" {
  command = plan

  variables {
    secret_id = "has spaces"
  }

  expect_failures = [var.secret_id]
}
