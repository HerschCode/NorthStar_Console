output "secret_id" {
  description = "Short secret ID, as expected by Cloud Run secret_key_ref."
  value       = google_secret_manager_secret.this.secret_id
}

output "id" {
  description = "Full resource ID (projects/<project>/secrets/<secret_id>)."
  value       = google_secret_manager_secret.this.id
}

output "accessor_member" {
  description = "The single principal allowed to read the payload."
  value       = local.accessor_member
}
