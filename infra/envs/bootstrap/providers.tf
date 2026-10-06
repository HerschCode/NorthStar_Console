provider "google" {
  project = var.project_id
  region  = var.region

  # The Billing Budgets API refuses user credentials unless a quota project is named. Harmless
  # (and still correct) when a service account is used instead.
  user_project_override = true
  billing_project       = var.project_id

  default_labels = {
    managed_by  = "terraform"
    repo        = "northstar-infra"
    layer       = "bootstrap"
    environment = var.environment
  }
}
