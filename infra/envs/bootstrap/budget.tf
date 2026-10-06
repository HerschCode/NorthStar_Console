# Applied first, before anything that could cost money. It lives in this human-only root so that
# the CI identities can neither see nor change it: a pipeline that could edit the budget could also
# silence the alarm that tells you it is misbehaving.

module "budget_guard" {
  source = "../../modules/budget_guard"

  billing_account_id = var.billing_account_id
  project_number     = var.project_number
  display_name       = "${var.project_id} monthly budget"
  currency_code      = var.currency_code
  budget_amount      = var.budget_amount
  alert_amounts      = var.alert_amounts

  depends_on = [google_project_service.apis]
}
