# Detective controls for the identity plane. Admin *writes* are always logged; these turn on the
# rest so the logs can answer "who exchanged a GitHub token, who impersonated which service
# account, and who read or changed which secret?". Volume is tiny (a handful of events per deploy),
# well inside the free Cloud Logging allowance.

locals {
  audited_services = [
    "sts.googleapis.com",            # workload identity federation token exchange (GitHub -> Google)
    "iamcredentials.googleapis.com", # service account impersonation / short-lived credential minting
    "secretmanager.googleapis.com",  # AccessSecretVersion, AddSecretVersion, IAM changes
  ]
}

resource "google_project_iam_audit_config" "identity_plane" {
  for_each = toset(local.audited_services)

  project = var.project_id
  service = each.value

  audit_log_config {
    log_type = "ADMIN_READ"
  }

  audit_log_config {
    log_type = "DATA_READ"
  }

  audit_log_config {
    log_type = "DATA_WRITE"
  }

  depends_on = [google_project_service.apis]
}
