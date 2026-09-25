output "services" {
  description = "Cloud Run service names keyed by role (gateway, assistant, performance)."
  value       = local.services
}

output "job" {
  description = "Cloud Run job name for the data pipeline."
  value       = local.job
}

output "service_hosts" {
  description = "Deterministic Cloud Run hostnames (<service>-<project number>.<region>.run.app), known before the services exist."
  value       = { for k, n in local.services : k => "${n}-${var.project_number}.${var.region}.run.app" }
}

output "service_urls" {
  description = "Deterministic Cloud Run URLs. Also the audience of the ID token a caller must present."
  value       = { for k, n in local.services : k => "https://${n}-${var.project_number}.${var.region}.run.app" }
}

output "runtime_service_account_ids" {
  description = "Account IDs of the runtime identities."
  value       = local.runtime_service_account_ids
}

output "ci_service_account_ids" {
  description = "Account IDs of the pipeline identities."
  value       = local.ci_service_account_ids
}

output "runtime_service_account_emails" {
  description = "Emails of the runtime identities."
  value       = { for k, id in local.runtime_service_account_ids : k => "${id}@${local.suffix}" }
}

output "ci_service_account_emails" {
  description = "Emails of the pipeline identities."
  value       = { for k, id in local.ci_service_account_ids : k => "${id}@${local.suffix}" }
}

output "runtime_members" {
  description = "IAM member strings of the runtime identities."
  value       = { for k, id in local.runtime_service_account_ids : k => "serviceAccount:${id}@${local.suffix}" }
}

output "ci_members" {
  description = "IAM member strings of the pipeline identities."
  value       = { for k, id in local.ci_service_account_ids : k => "serviceAccount:${id}@${local.suffix}" }
}
