output "repository_id" {
  description = "Repository ID."
  value       = google_artifact_registry_repository.this.repository_id
}

output "image_prefix" {
  description = "Prefix to put in front of an image name: <location>-docker.pkg.dev/<project>/<repository>."
  value       = "${var.location}-docker.pkg.dev/${var.project_id}/${var.repository_id}"
}
