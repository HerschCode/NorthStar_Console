locals {
  accessor_member  = "serviceAccount:${var.accessor_service_account_email}"
  auto_replication = length(var.replica_locations) == 0
}

resource "google_secret_manager_secret" "this" {
  project             = var.project_id
  secret_id           = var.secret_id
  labels              = var.labels
  deletion_protection = var.deletion_protection

  replication {
    dynamic "auto" {
      for_each = local.auto_replication ? [1] : []
      content {}
    }

    dynamic "user_managed" {
      for_each = local.auto_replication ? [] : [1]
      content {
        dynamic "replicas" {
          for_each = toset(var.replica_locations)
          content {
            location = replicas.value
          }
        }
      }
    }
  }
}

# Authoritative for the accessor role on this one secret: a reader added out of band is drift
# and is removed on the next apply. `secret_id` (a configured value) is used rather than `.id`
# so the binding is fully known at plan time.
resource "google_secret_manager_secret_iam_binding" "accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.this.secret_id
  role      = "roles/secretmanager.secretAccessor"
  members   = [local.accessor_member]
}

resource "google_secret_manager_secret_version" "placeholder" {
  count = var.create_placeholder_version ? 1 : 0

  secret                 = google_secret_manager_secret.this.id
  secret_data_wo         = "placeholder-replace-with-gcloud-secrets-versions-add"
  secret_data_wo_version = "1"
}
