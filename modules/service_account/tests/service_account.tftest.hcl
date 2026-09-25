# Offline unit tests: the google provider is mocked, so no credentials or network are needed.
#   terraform -chdir=modules/service_account test
mock_provider "google" {}

variables {
  project_id   = "northstar-test"
  account_id   = "gateway-sa"
  display_name = "Gateway runtime"
}

run "identifiers_are_known_at_plan_time" {
  command = plan

  assert {
    condition     = output.email == "gateway-sa@northstar-test.iam.gserviceaccount.com"
    error_message = "email must be derived from account_id and project_id"
  }

  assert {
    condition     = output.member == "serviceAccount:gateway-sa@northstar-test.iam.gserviceaccount.com"
    error_message = "member must be serviceAccount:<email>"
  }

  assert {
    condition     = output.name == "projects/northstar-test/serviceAccounts/gateway-sa@northstar-test.iam.gserviceaccount.com"
    error_message = "name must be the fully-qualified resource name"
  }
}

run "no_project_roles_by_default" {
  command = plan

  assert {
    condition     = length(google_project_iam_member.project_roles) == 0
    error_message = "a service account gets no project-level roles unless asked"
  }

  assert {
    condition     = length(google_service_account_iam_binding.service_account_user) == 0
    error_message = "no actAs binding unless members are given"
  }
}

run "grants_only_the_requested_project_roles" {
  command = plan

  variables {
    project_roles = ["roles/bigquery.jobUser"]
  }

  assert {
    condition     = length(google_project_iam_member.project_roles) == 1
    error_message = "exactly one project-level binding expected"
  }

  assert {
    condition     = google_project_iam_member.project_roles["roles/bigquery.jobUser"].member == "serviceAccount:gateway-sa@northstar-test.iam.gserviceaccount.com"
    error_message = "binding member must be this service account"
  }
}

run "act_as_binding_is_scoped_to_this_service_account" {
  command = plan

  variables {
    service_account_user_members = ["serviceAccount:deployer@northstar-test.iam.gserviceaccount.com"]
  }

  assert {
    condition     = google_service_account_iam_binding.service_account_user[0].role == "roles/iam.serviceAccountUser"
    error_message = "actAs must be roles/iam.serviceAccountUser"
  }

  assert {
    condition     = google_service_account_iam_binding.service_account_user[0].service_account_id == output.name
    error_message = "actAs must be granted on this service account, not the project"
  }
}

run "rejects_primitive_owner" {
  command = plan

  variables {
    project_roles = ["roles/owner"]
  }

  expect_failures = [var.project_roles]
}

run "rejects_primitive_editor" {
  command = plan

  variables {
    project_roles = ["roles/editor"]
  }

  expect_failures = [var.project_roles]
}

run "rejects_primitive_viewer" {
  command = plan

  variables {
    project_roles = ["roles/viewer"]
  }

  expect_failures = [var.project_roles]
}

run "rejects_malformed_account_id" {
  command = plan

  variables {
    account_id = "Bad_ID"
  }

  expect_failures = [var.account_id]
}
