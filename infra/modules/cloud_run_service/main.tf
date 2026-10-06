resource "google_cloud_run_v2_service" "this" {
  project             = var.project_id
  name                = var.name
  location            = var.region
  description         = var.description
  ingress             = var.ingress
  labels              = var.labels
  deletion_protection = var.deletion_protection

  template {
    service_account                  = var.service_account_email
    timeout                          = "${var.timeout_seconds}s"
    max_instance_request_concurrency = var.max_concurrency

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = var.image

      ports {
        container_port = var.container_port
      }

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        # Request-based billing: CPU is only charged while a request is being handled.
        cpu_idle          = true
        startup_cpu_boost = true
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

      dynamic "startup_probe" {
        for_each = var.startup_probe_path == null ? [] : [var.startup_probe_path]
        content {
          initial_delay_seconds = 0
          timeout_seconds       = 5
          period_seconds        = 10
          # ~3 minutes: the Python images (numpy / onnx / mlflow) start slowly on a cold instance.
          failure_threshold = 18

          http_get {
            path = startup_probe.value
          }
        }
      }
    }
  }

  # App repos deploy with `gcloud run deploy --image ...`, which also stamps client metadata and
  # may add revision labels/annotations. None of that is infrastructure drift.
  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      template[0].labels,
      template[0].annotations,
      client,
      client_version,
    ]
  }
}

# Authoritative for the invoker role on this one service. A caller added out of band (console,
# gcloud) is drift and is removed on the next apply.
resource "google_cloud_run_v2_service_iam_binding" "invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.this.name
  role     = "roles/run.invoker"
  members  = sort(tolist(var.invoker_members))

  lifecycle {
    precondition {
      condition     = var.allow_public || !contains(var.invoker_members, "allUsers")
      error_message = "`allUsers` in invoker_members requires allow_public = true. Only the gateway is meant to be public."
    }
  }
}

resource "google_cloud_run_v2_service_iam_binding" "developer" {
  count = length(var.deployer_members) > 0 ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.this.name
  role     = "roles/run.developer"
  members  = sort(tolist(var.deployer_members))
}
