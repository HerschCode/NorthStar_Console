output "email" {
  description = "Service account email. Derived from the inputs, so it is known at plan time."
  value       = local.email

  depends_on = [google_service_account.this]
}

output "member" {
  description = "IAM member string (serviceAccount:<email>) for use in bindings."
  value       = local.member

  depends_on = [google_service_account.this]
}

output "name" {
  description = "Fully-qualified resource name (projects/<project>/serviceAccounts/<email>), as expected by google_service_account_iam_* resources."
  value       = local.name

  depends_on = [google_service_account.this]
}

output "account_id" {
  description = "The account ID (the part of the email before the @)."
  value       = var.account_id
}
