output "dataset_id" {
  description = "Dataset ID."
  value       = google_bigquery_dataset.this.dataset_id
}

output "id" {
  description = "Resource ID (projects/<project>/datasets/<dataset>)."
  value       = google_bigquery_dataset.this.id
}

output "table_ids" {
  description = "Fully-qualified table IDs (<project>.<dataset>.<table>), keyed by table name."
  value       = { for k, t in google_bigquery_table.this : k => "${var.project_id}.${var.dataset_id}.${k}" }
}
