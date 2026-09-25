output "name" {
  description = "Job name."
  value       = google_cloud_run_v2_job.this.name
}

output "id" {
  description = "Resource ID (projects/<project>/locations/<region>/jobs/<name>)."
  value       = google_cloud_run_v2_job.this.id
}

output "run_uri" {
  description = "The Cloud Run Admin API endpoint that starts an execution (what Cloud Scheduler POSTs to)."
  value       = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${var.name}:run"
}

output "scheduler_job_name" {
  description = "Name of the Cloud Scheduler job, or null when the job is unscheduled."
  value       = one(google_cloud_scheduler_job.trigger[*].name)
}
