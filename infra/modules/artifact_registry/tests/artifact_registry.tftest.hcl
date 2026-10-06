mock_provider "google" {}

variables {
  project_id    = "northstar-test"
  location      = "us-central1"
  repository_id = "northstar"
}

run "image_prefix" {
  command = plan

  assert {
    condition     = output.image_prefix == "us-central1-docker.pkg.dev/northstar-test/northstar"
    error_message = "image_prefix must be <location>-docker.pkg.dev/<project>/<repo>"
  }
}

run "scanning_is_explicitly_disabled" {
  command = plan

  assert {
    condition     = google_artifact_registry_repository.this.vulnerability_scanning_config[0].enablement_config == "DISABLED"
    error_message = "Artifact Analysis scanning is billed; keep it off explicitly"
  }
}

run "cleanup_keeps_recent_versions" {
  command = plan

  variables {
    keep_recent_versions = 5
  }

  assert {
    condition = anytrue([
      for p in google_artifact_registry_repository.this.cleanup_policies :
      p.action == "KEEP" && p.most_recent_versions[0].keep_count == 5
    ])
    error_message = "a KEEP policy for the N most recent versions must exist"
  }

  assert {
    condition     = google_artifact_registry_repository.this.cleanup_policy_dry_run == false
    error_message = "cleanup must actually delete, not dry-run"
  }
}

run "cleanup_ages_are_in_seconds" {
  command = plan

  variables {
    delete_untagged_after_days = 2
  }

  assert {
    condition = anytrue([
      for p in google_artifact_registry_repository.this.cleanup_policies :
      p.id == "delete-untagged" && p.condition[0].older_than == "172800s"
    ])
    error_message = "2 days must render as 172800s"
  }
}

run "no_writer_binding_by_default" {
  command = plan

  assert {
    condition     = length(google_artifact_registry_repository_iam_binding.writers) == 0
    error_message = "nobody may push unless listed"
  }
}

run "writer_binding_is_repository_scoped" {
  command = plan

  variables {
    writer_members = ["serviceAccount:gh-deploy-gateway@northstar-test.iam.gserviceaccount.com"]
  }

  assert {
    condition     = google_artifact_registry_repository_iam_binding.writers[0].role == "roles/artifactregistry.writer"
    error_message = "writers get artifactregistry.writer, nothing broader"
  }

  assert {
    condition     = google_artifact_registry_repository_iam_binding.writers[0].repository == "northstar"
    error_message = "binding must target this repository, not the project"
  }
}

run "rejects_zero_kept_versions" {
  command = plan

  variables {
    keep_recent_versions = 0
  }

  expect_failures = [var.keep_recent_versions]
}
