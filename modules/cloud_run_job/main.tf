locals {
  scheduled = var.schedule != null
}

resource "google_cloud_run_v2_job" "this" {
  project             = var.project_id
  name                = var.name
  location            = var.region
  labels              = var.labels
  deletion_protection = var.deletion_protection

  template {
    task_count = var.task_count

    template {
      service_account = var.service_account_email
      timeout         = "${var.timeout_seconds}s"
      max_retries     = var.max_retries

      containers {
        image   = var.image
        command = var.command
        args    = var.args

        resources {
          limits = {
            cpu    = var.cpu
            memory = var.memory
          }
        }

        dynamic "env" {
          for_each = var.env
          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = var.secret_env
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = env.value.secret_id
                version = env.value.version
              }
            }
          }
        }
      }
    }
  }

  # The app repo's CI owns the image; client metadata and revision labels are not drift.
  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
      template[0].labels,
      template[0].annotations,
      client,
      client_version,
    ]
  }
}

# Authoritative for the invoker role on this one job.
resource "google_cloud_run_v2_job_iam_binding" "invoker" {
  count = length(var.invoker_members) > 0 ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.this.name
  role     = "roles/run.invoker"
  members  = sort(tolist(var.invoker_members))
}

resource "google_cloud_run_v2_job_iam_binding" "developer" {
  count = length(var.deployer_members) > 0 ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.this.name
  role     = "roles/run.developer"
  members  = sort(tolist(var.deployer_members))
}

# Cloud Scheduler calls the Cloud Run Admin API's `jobs:run` method. Google APIs need an OAuth
# access token (an OIDC token is only for calling your own Cloud Run services), and the
# scheduler's service account needs roles/run.invoker on the job, granted above.
resource "google_cloud_scheduler_job" "trigger" {
  count = local.scheduled ? 1 : 0

  project          = var.project_id
  region           = coalesce(var.scheduler_region, var.region)
  name             = "${var.name}-trigger"
  description      = "Runs the ${var.name} Cloud Run job"
  schedule         = var.schedule
  time_zone        = var.schedule_time_zone
  paused           = var.schedule_paused
  attempt_deadline = "180s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${var.name}:run"

    oauth_token {
      service_account_email = var.scheduler_service_account_email
    }
  }

  lifecycle {
    precondition {
      condition     = var.scheduler_service_account_email != null
      error_message = "scheduler_service_account_email is required when schedule is set."
    }

    precondition {
      condition     = var.scheduler_service_account_email == null ? true : contains(var.invoker_members, "serviceAccount:${var.scheduler_service_account_email}")
      error_message = "The scheduler's service account must be listed in invoker_members, otherwise Cloud Scheduler gets 403 when it runs the job."
    }
  }
}
