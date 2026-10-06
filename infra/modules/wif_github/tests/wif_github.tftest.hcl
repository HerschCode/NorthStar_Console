mock_provider "google" {}

variables {
  project_id          = "northstar-test"
  project_number      = "123456789012"
  github_owner        = "HerschCode"
  github_repositories = ["HerschCode/northstar-infra", "HerschCode/operations-assistant"]
}

run "identifiers_are_known_at_plan_time" {
  command = plan

  assert {
    condition     = output.provider_name == "projects/123456789012/locations/global/workloadIdentityPools/github/providers/github-oidc"
    error_message = "provider_name must be the full resource name expected by google-github-actions/auth"
  }

  assert {
    condition     = output.pool_name == "projects/123456789012/locations/global/workloadIdentityPools/github"
    error_message = "pool_name must be the full pool resource name"
  }
}

run "provider_only_accepts_the_listed_repositories" {
  command = plan

  assert {
    condition     = strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, "assertion.repository_owner == \"HerschCode\"")
    error_message = "the owner must be pinned"
  }

  assert {
    condition     = strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, "\"HerschCode/northstar-infra\"") && strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, "\"HerschCode/operations-assistant\"")
    error_message = "every listed repository must appear in the condition"
  }

  assert {
    condition     = google_iam_workload_identity_pool_provider.github.oidc[0].issuer_uri == "https://token.actions.githubusercontent.com"
    error_message = "the GitHub Actions OIDC issuer is required"
  }
}

run "condition_is_never_empty" {
  command = plan

  assert {
    condition     = length(google_iam_workload_identity_pool_provider.github.attribute_condition) > 0
    error_message = "an empty condition would accept tokens from any GitHub repository"
  }
}

run "branch_pinned_principal" {
  command = plan

  variables {
    impersonation = {
      deploy = {
        service_account_name = "projects/northstar-test/serviceAccounts/gh-deploy@northstar-test.iam.gserviceaccount.com"
        allowed              = [{ repository = "HerschCode/operations-assistant", ref = "refs/heads/main" }]
      }
    }
  }

  assert {
    condition     = one(google_service_account_iam_binding.workload_identity_user["deploy"].members) == "principalSet://iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/github/attribute.repo_ref/HerschCode/operations-assistant/refs/heads/main"
    error_message = "the principal must pin repository and branch"
  }

  assert {
    condition     = google_service_account_iam_binding.workload_identity_user["deploy"].role == "roles/iam.workloadIdentityUser"
    error_message = "only workloadIdentityUser may be granted"
  }
}

run "environment_pinned_principal" {
  command = plan

  variables {
    impersonation = {
      apply = {
        service_account_name = "projects/northstar-test/serviceAccounts/gh-tf-apply@northstar-test.iam.gserviceaccount.com"
        allowed              = [{ repository = "HerschCode/northstar-infra", environment = "dev-apply" }]
      }
    }
  }

  assert {
    condition     = one(google_service_account_iam_binding.workload_identity_user["apply"].members) == "principalSet://iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/github/attribute.repo_env/HerschCode/northstar-infra/dev-apply"
    error_message = "the principal must pin repository and GitHub environment"
  }
}

run "repository_only_principal_when_nothing_else_is_given" {
  command = plan

  variables {
    impersonation = {
      plan = {
        service_account_name = "projects/northstar-test/serviceAccounts/gh-tf-plan@northstar-test.iam.gserviceaccount.com"
        allowed              = [{ repository = "HerschCode/northstar-infra" }]
      }
    }
  }

  assert {
    condition     = one(google_service_account_iam_binding.workload_identity_user["plan"].members) == "principalSet://iam.googleapis.com/projects/123456789012/locations/global/workloadIdentityPools/github/attribute.repository/HerschCode/northstar-infra"
    error_message = "a repository-scoped principal is the fallback"
  }
}

run "bindings_are_service_account_scoped" {
  command = plan

  variables {
    impersonation = {
      deploy = {
        service_account_name = "projects/northstar-test/serviceAccounts/gh-deploy@northstar-test.iam.gserviceaccount.com"
        allowed              = [{ repository = "HerschCode/operations-assistant", ref = "refs/heads/main" }]
      }
    }
  }

  assert {
    condition     = google_service_account_iam_binding.workload_identity_user["deploy"].service_account_id == "projects/northstar-test/serviceAccounts/gh-deploy@northstar-test.iam.gserviceaccount.com"
    error_message = "the grant must sit on the service account itself, never the project"
  }
}

run "rejects_repository_outside_the_pool_allow_list" {
  command = plan

  variables {
    impersonation = {
      deploy = {
        service_account_name = "projects/northstar-test/serviceAccounts/gh-deploy@northstar-test.iam.gserviceaccount.com"
        allowed              = [{ repository = "HerschCode/some-other-repo", ref = "refs/heads/main" }]
      }
    }
  }

  expect_failures = [var.impersonation]
}

run "rejects_ref_and_environment_together" {
  command = plan

  variables {
    impersonation = {
      deploy = {
        service_account_name = "projects/northstar-test/serviceAccounts/gh-deploy@northstar-test.iam.gserviceaccount.com"
        allowed              = [{ repository = "HerschCode/operations-assistant", ref = "refs/heads/main", environment = "prod" }]
      }
    }
  }

  expect_failures = [var.impersonation]
}

run "rejects_short_branch_names" {
  command = plan

  variables {
    impersonation = {
      deploy = {
        service_account_name = "projects/northstar-test/serviceAccounts/gh-deploy@northstar-test.iam.gserviceaccount.com"
        allowed              = [{ repository = "HerschCode/operations-assistant", ref = "main" }]
      }
    }
  }

  expect_failures = [var.impersonation]
}

run "rejects_repository_owned_by_someone_else" {
  command = plan

  variables {
    github_repositories = ["attacker/northstar-infra"]
  }

  expect_failures = [var.github_repositories]
}

run "rejects_project_id_as_project_number" {
  command = plan

  variables {
    project_number = "northstar-test"
  }

  expect_failures = [var.project_number]
}
