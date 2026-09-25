resource "google_bigquery_dataset" "this" {
  project                     = var.project_id
  dataset_id                  = var.dataset_id
  location                    = var.location
  description                 = var.description
  labels                      = var.labels
  delete_contents_on_destroy  = var.delete_contents_on_destroy
  default_table_expiration_ms = var.default_table_expiration_ms
}

resource "google_bigquery_table" "this" {
  # checkov:skip=CKV_GCP_121: Protection is a module variable that defaults to ON. The dev environment turns it off on purpose so `terraform destroy` leaves nothing billable behind (see docs/runbook.md, teardown). Any environment that holds data worth keeping must leave it on.
  for_each = var.tables

  project                  = var.project_id
  dataset_id               = google_bigquery_dataset.this.dataset_id
  table_id                 = each.key
  description              = each.value.description
  schema                   = each.value.schema
  clustering               = length(each.value.clustering) > 0 ? each.value.clustering : null
  require_partition_filter = each.value.require_partition_filter
  deletion_protection      = var.deletion_protection

  dynamic "time_partitioning" {
    for_each = each.value.time_partitioning == null ? [] : [each.value.time_partitioning]
    content {
      type          = time_partitioning.value.type
      field         = time_partitioning.value.field
      expiration_ms = time_partitioning.value.expiration_ms
    }
  }
}

# Dataset-level grants only. The project-level half of "can run a query" (roles/bigquery.jobUser)
# lives on the service account module, because GCP defines job creation at project scope.
# Authoritative per role: a reader or editor added out of band is drift and gets removed.
resource "google_bigquery_dataset_iam_binding" "viewers" {
  count = length(var.viewer_members) > 0 ? 1 : 0

  project    = var.project_id
  dataset_id = google_bigquery_dataset.this.dataset_id
  role       = "roles/bigquery.dataViewer"
  members    = sort(tolist(var.viewer_members))
}

resource "google_bigquery_dataset_iam_binding" "editors" {
  count = length(var.editor_members) > 0 ? 1 : 0

  project    = var.project_id
  dataset_id = google_bigquery_dataset.this.dataset_id
  role       = "roles/bigquery.dataEditor"
  members    = sort(tolist(var.editor_members))
}
