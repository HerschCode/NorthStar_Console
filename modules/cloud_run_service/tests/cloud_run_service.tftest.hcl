mock_provider "google" {}

variables {
  project_id            = "northstar-test"
  region                = "us-central1"
  name                  = "operations-assistant"
  image                 = "us-docker.pkg.dev/cloudrun/container/hello"
  service_account_email = "assistant-sa@northstar-test.iam.gserviceaccount.com"
  invoker_members       = ["serviceAccount:gateway-sa@northstar-test.iam.gserviceaccount.com"]
}

run "safe_defaults" {
  command = plan

  assert {
    condition     = google_cloud_run_v2_service.this.template[0].scaling[0].min_instance_count == 0
    error_message = "must scale to zero by default"
  }

  assert {
    condition     = google_cloud_run_v2_service.this.template[0].containers[0].resources[0].cpu_idle == true
    error_message = "request-based CPU billing expected"
  }

  assert {
    condition     = google_cloud_run_v2_service.this.deletion_protection == false
    error_message = "dev environments must tear down cleanly"
  }

  assert {
    condition     = google_cloud_run_v2_service.this.template[0].service_account == "assistant-sa@northstar-test.iam.gserviceaccount.com"
    error_message = "the service must run as the dedicated service account"
  }

  assert {
    condition     = length(google_cloud_run_v2_service.this.template[0].containers[0].startup_probe) == 0
    error_message = "default startup probe is Cloud Run's TCP probe"
  }
}

run "private_service_has_only_the_listed_invoker" {
  command = plan

  assert {
    condition     = google_cloud_run_v2_service_iam_binding.invoker.role == "roles/run.invoker"
    error_message = "invoker role expected"
  }

  assert {
    condition     = length(google_cloud_run_v2_service_iam_binding.invoker.members) == 1
    error_message = "exactly the listed invoker"
  }

  assert {
    condition     = !contains(google_cloud_run_v2_service_iam_binding.invoker.members, "allUsers")
    error_message = "a private service must never list allUsers"
  }
}

run "public_needs_both_keys" {
  command = plan

  variables {
    invoker_members = ["allUsers"]
    allow_public    = true
  }

  assert {
    condition     = contains(google_cloud_run_v2_service_iam_binding.invoker.members, "allUsers")
    error_message = "allUsers accepted when allow_public is set"
  }
}

run "allUsers_without_allow_public_is_blocked" {
  command = plan

  variables {
    invoker_members = ["allUsers"]
  }

  expect_failures = [google_cloud_run_v2_service_iam_binding.invoker]
}

run "allAuthenticatedUsers_is_never_accepted" {
  command = plan

  variables {
    invoker_members = ["allAuthenticatedUsers"]
    allow_public    = true
  }

  expect_failures = [var.invoker_members]
}

run "empty_invoker_list_is_rejected" {
  command = plan

  variables {
    invoker_members = []
  }

  expect_failures = [var.invoker_members]
}

run "default_compute_service_account_is_rejected" {
  command = plan

  variables {
    service_account_email = "123456789012-compute@developer.gserviceaccount.com"
  }

  expect_failures = [var.service_account_email]
}

run "plain_and_secret_env_are_rendered" {
  command = plan

  variables {
    env = {
      AGENT_PROVIDER = "groq"
      LOG_LEVEL      = "info"
    }
    secret_env = {
      GROQ_API_KEY = { secret_id = "groq-api-key" }
    }
  }

  assert {
    condition     = length(google_cloud_run_v2_service.this.template[0].containers[0].env) == 3
    error_message = "two plain variables and one secret expected"
  }

  assert {
    condition = one([
      for e in google_cloud_run_v2_service.this.template[0].containers[0].env :
      e.value_source[0].secret_key_ref[0].secret if e.name == "GROQ_API_KEY"
    ]) == "groq-api-key"
    error_message = "GROQ_API_KEY must come from Secret Manager"
  }

  assert {
    condition = one([
      for e in google_cloud_run_v2_service.this.template[0].containers[0].env :
      e.value_source[0].secret_key_ref[0].version if e.name == "GROQ_API_KEY"
    ]) == "latest"
    error_message = "secret version defaults to latest"
  }
}

run "reserved_PORT_is_rejected" {
  command = plan

  variables {
    env = { PORT = "9000" }
  }

  expect_failures = [var.env]
}

run "same_name_in_env_and_secret_env_is_rejected" {
  command = plan

  variables {
    env        = { API_KEY = "x" }
    secret_env = { API_KEY = { secret_id = "some-secret" } }
  }

  expect_failures = [var.env]
}

run "http_startup_probe_when_path_given" {
  command = plan

  variables {
    startup_probe_path = "/health"
  }

  assert {
    condition     = google_cloud_run_v2_service.this.template[0].containers[0].startup_probe[0].http_get[0].path == "/health"
    error_message = "startup probe must hit the given path"
  }
}

run "developer_binding_only_when_deployers_given" {
  command = plan

  assert {
    condition     = length(google_cloud_run_v2_service_iam_binding.developer) == 0
    error_message = "nobody may deploy unless listed"
  }
}

run "developer_binding_is_service_scoped" {
  command = plan

  variables {
    deployer_members = ["serviceAccount:gh-deploy-assistant@northstar-test.iam.gserviceaccount.com"]
  }

  assert {
    condition     = google_cloud_run_v2_service_iam_binding.developer[0].role == "roles/run.developer"
    error_message = "deployers get run.developer"
  }

  assert {
    condition     = google_cloud_run_v2_service_iam_binding.developer[0].name == "operations-assistant"
    error_message = "the binding must target this one service"
  }
}

run "rejects_unsupported_cpu" {
  command = plan

  variables {
    cpu = "0.5"
  }

  expect_failures = [var.cpu]
}
