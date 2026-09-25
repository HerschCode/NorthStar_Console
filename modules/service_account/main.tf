locals {
  # A service account's email, member string and resource name are pure functions of
  # (account_id, project_id). Deriving them here, instead of reading them off the resource,
  # keeps them *known at plan time*. That matters for policy-as-code: conftest evaluates
  # `terraform show -json` output, and an IAM binding whose member is "known after apply"
  # cannot be checked. The depends_on on each output preserves creation ordering.
  email  = "${var.account_id}@${var.project_id}.iam.gserviceaccount.com"
  member = "serviceAccount:${local.email}"
  name   = "projects/${var.project_id}/serviceAccounts/${local.email}"
}

resource "google_service_account" "this" {
  project      = var.project_id
  account_id   = var.account_id
  display_name = var.display_name
  description  = var.description
}

resource "google_project_iam_member" "project_roles" {
  for_each = var.project_roles

  project = var.project_id
  role    = each.value
  member  = local.member

  depends_on = [google_service_account.this]
}

# Authoritative for this one role on this one service account: anyone added out of band
# shows up as drift and is removed on the next apply.
resource "google_service_account_iam_binding" "service_account_user" {
  count = length(var.service_account_user_members) > 0 ? 1 : 0

  service_account_id = local.name
  role               = "roles/iam.serviceAccountUser"
  members            = sort(tolist(var.service_account_user_members))

  depends_on = [google_service_account.this]
}
