resource "google_artifact_registry_repository" "this" {
  project       = var.project_id
  location      = var.location
  repository_id = var.repository_id
  format        = "DOCKER"
  description   = var.description
  labels        = var.labels

  docker_config {
    immutable_tags = var.immutable_tags
  }

  # Artifact Analysis scanning is billed per image. Image scanning is done by Trivy in the app
  # repos' CI (and gates the deploy), so it is switched off here explicitly rather than left to
  # be turned on by enabling an API later.
  vulnerability_scanning_config {
    enablement_config = "DISABLED"
  }

  # A KEEP policy wins over a DELETE policy, so the newest `keep_recent_versions` of every image
  # survive even when they are older than `delete_older_than_days`.
  cleanup_policy_dry_run = false

  cleanup_policies {
    id     = "delete-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "${var.delete_untagged_after_days * 86400}s"
    }
  }

  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      tag_state  = "ANY"
      older_than = "${var.delete_older_than_days * 86400}s"
    }
  }

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = var.keep_recent_versions
    }
  }
}

# Authoritative for the writer role on this one repository.
resource "google_artifact_registry_repository_iam_binding" "writers" {
  count = length(var.writer_members) > 0 ? 1 : 0

  project    = var.project_id
  location   = var.location
  repository = google_artifact_registry_repository.this.repository_id
  role       = "roles/artifactregistry.writer"
  members    = sort(tolist(var.writer_members))
}
