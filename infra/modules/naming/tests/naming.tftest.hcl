variables {
  project_id     = "northstar-dev"
  project_number = "123456789012"
  region         = "us-central1"
}

run "cloud_run_urls_are_deterministic" {
  command = plan

  assert {
    condition     = output.service_urls["gateway"] == "https://llm-security-gateway-123456789012.us-central1.run.app"
    error_message = "URL must be https://<service>-<project number>.<region>.run.app"
  }

  assert {
    condition     = output.service_hosts["performance"] == "operations-performance-123456789012.us-central1.run.app"
    error_message = "host must be the URL without its scheme"
  }
}

run "emails_and_members_are_consistent" {
  command = plan

  assert {
    condition     = output.runtime_service_account_emails["performance"] == "perf-sa@northstar-dev.iam.gserviceaccount.com"
    error_message = "email must be <account id>@<project>.iam.gserviceaccount.com"
  }

  assert {
    condition     = output.runtime_members["performance"] == "serviceAccount:${output.runtime_service_account_emails["performance"]}"
    error_message = "member must be serviceAccount:<email>"
  }

  assert {
    condition     = output.ci_members["terraform_apply"] == "serviceAccount:gh-tf-apply@northstar-dev.iam.gserviceaccount.com"
    error_message = "CI members must follow the same convention"
  }
}

run "every_account_id_is_a_valid_service_account_id" {
  command = plan

  assert {
    condition = alltrue([
      for id in concat(values(output.runtime_service_account_ids), values(output.ci_service_account_ids)) :
      can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", id))
    ])
    error_message = "account IDs must be 6-30 characters of lowercase letters, digits and hyphens"
  }
}

run "no_two_identities_share_an_account_id" {
  command = plan

  assert {
    condition     = length(distinct(concat(values(output.runtime_service_account_ids), values(output.ci_service_account_ids)))) == length(output.runtime_service_account_ids) + length(output.ci_service_account_ids)
    error_message = "account IDs must be unique across the runtime and pipeline planes"
  }
}

run "rejects_project_id_as_project_number" {
  command = plan

  variables {
    project_number = "northstar-dev"
  }

  expect_failures = [var.project_number]
}
