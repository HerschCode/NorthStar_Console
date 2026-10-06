output "name" {
  description = "Service name."
  value       = google_cloud_run_v2_service.this.name
}

output "id" {
  description = "Resource ID (projects/<project>/locations/<region>/services/<name>)."
  value       = google_cloud_run_v2_service.this.id
}

output "uri" {
  description = "Default HTTPS URL of the service (known after apply)."
  value       = google_cloud_run_v2_service.this.uri
}

output "service_account_email" {
  description = "Runtime identity of the service."
  value       = var.service_account_email
}

output "invoker_members" {
  description = "Principals allowed to call the service."
  value       = var.invoker_members
}
