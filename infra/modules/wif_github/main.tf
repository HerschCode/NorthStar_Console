locals {
  # Known at plan time (unlike google_iam_workload_identity_pool.name), so the principal
  # strings below can be checked by policy-as-code on a first plan.
  pool_name     = "projects/${var.project_number}/locations/global/workloadIdentityPools/${var.pool_id}"
  provider_name = "${local.pool_name}/providers/${var.provider_id}"

  # Rendered as a CEL list literal: ["owner/a","owner/b"]
  repository_list = jsonencode(sort(tolist(var.github_repositories)))

  principal_prefix = "principalSet://iam.googleapis.com/${local.pool_name}"

  # service account key => list of principalSet members
  members = {
    for k, v in var.impersonation : k => sort([
      for a in v.allowed :
      a.ref != null ? "${local.principal_prefix}/attribute.repo_ref/${a.repository}/${a.ref}" :
      a.environment != null ? "${local.principal_prefix}/attribute.repo_env/${a.repository}/${a.environment}" :
      "${local.principal_prefix}/attribute.repository/${a.repository}"
    ])
  }
}

resource "google_iam_workload_identity_pool" "this" {
  project                   = var.project_id
  workload_identity_pool_id = var.pool_id
  display_name              = "GitHub Actions"
  description               = "Keyless authentication for GitHub Actions. No service account keys exist anywhere."
}

resource "google_iam_workload_identity_pool_provider" "github" {
  # checkov:skip=CKV_GCP_125: The check only recognises conditions of the form `assertion.sub == "repo:owner/repo:..."`. This provider serves several repositories and pins the owner AND an explicit repository allow-list on `assertion.repository`, which is at least as strict. The equivalent invariant is enforced on every plan by conftest rule WIF-001 (and WIF-002 for the grants).
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.this.workload_identity_pool_id
  workload_identity_pool_provider_id = var.provider_id
  display_name                       = "GitHub OIDC"
  description                        = "Accepts GitHub Actions OIDC tokens only for the listed repositories."

  attribute_mapping = {
    "google.subject"             = "assertion.sub"
    "attribute.repository"       = "assertion.repository"
    "attribute.repository_owner" = "assertion.repository_owner"
    "attribute.ref"              = "assertion.ref"
    "attribute.repo_ref"         = "assertion.repository + '/' + assertion.ref"
    "attribute.repo_env"         = "assertion.repository + '/' + assertion.environment"
  }

  # Evaluated before any IAM binding: a token from another owner or repository never becomes a
  # Google credential, whatever the service-account bindings say.
  attribute_condition = "assertion.repository_owner == \"${var.github_owner}\" && assertion.repository in ${local.repository_list}"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# Authoritative for roles/iam.workloadIdentityUser on each service account: a principal added
# out of band is drift and is removed on the next apply. Always resource-level, never project-level.
resource "google_service_account_iam_binding" "workload_identity_user" {
  for_each = var.impersonation

  service_account_id = each.value.service_account_name
  role               = "roles/iam.workloadIdentityUser"
  members            = local.members[each.key]

  depends_on = [google_iam_workload_identity_pool_provider.github]
}
