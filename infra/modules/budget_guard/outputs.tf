output "budget_name" {
  description = "Resource name of the budget (billingAccounts/<id>/budgets/<id>)."
  value       = google_billing_budget.this.name
}

output "thresholds" {
  description = "Alert level (in currency_code) => fraction of the budget the API was given."
  value       = { for i, a in var.alert_amounts : tostring(a) => local.thresholds[i] }
}
