provider "google" {
  project = var.project_id
  region  = var.region

  # Bill API calls to the project's own quota rather than whichever project the caller's
  # credentials belong to. The CI identities carry serviceUsageConsumer for exactly this.
  user_project_override = true
  billing_project       = var.project_id

  default_labels = {
    managed_by  = "terraform"
    repo        = "northstar-infra"
    layer       = "workloads"
    environment = var.environment
  }
}
