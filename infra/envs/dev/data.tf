# The BigQuery half of operations-performance: the same two datasets and partitioned, clustered
# tables as cloud/bigquery/schema.sql there. Terraform creates the empty, correctly shaped tables
# first; the loader (scripts/migrate_to_bigquery.py) must then append to them, because a
# WRITE_TRUNCATE load into a table that does not exist yet creates a plain unpartitioned one.
#
# Types use BigQuery's legacy names (INTEGER, FLOAT), which is what the API returns, so Terraform
# never sees a spurious schema diff.

locals {
  bq_staging_dataset   = "operations_performance_staging"
  bq_analytics_dataset = "operations_performance_analytics"

  # Column type/mode presets; each column adds its own `name`.
  bq_field = {
    req_string = { type = "STRING", mode = "REQUIRED" }
    string     = { type = "STRING", mode = "NULLABLE" }
    req_ts     = { type = "TIMESTAMP", mode = "REQUIRED" }
    ts         = { type = "TIMESTAMP", mode = "NULLABLE" }
    integer    = { type = "INTEGER", mode = "NULLABLE" }
    float      = { type = "FLOAT", mode = "NULLABLE" }
    req_float  = { type = "FLOAT", mode = "REQUIRED" }
    json       = { type = "JSON", mode = "NULLABLE" }
  }

  bq_tables_staging = {
    events = {
      description = "Cleaned event log, one row per event."
      schema = jsonencode([
        merge(local.bq_field.req_string, { name = "case_id" }),
        merge(local.bq_field.req_string, { name = "activity" }),
        merge(local.bq_field.req_ts, { name = "timestamp" }),
        merge(local.bq_field.string, { name = "resource" }),
        merge(local.bq_field.string, { name = "purchase_order_id" }),
        merge(local.bq_field.string, { name = "item_id" }),
        merge(local.bq_field.string, { name = "category" }),
        merge(local.bq_field.string, { name = "supplier_id" }),
      ])
      time_partitioning = { field = "timestamp" }
      clustering        = ["case_id", "category"]
    }
  }

  bq_tables_analytics = {
    process_cases = {
      description = "One row per process case. case_id is a logical primary key (not enforced by BigQuery)."
      schema = jsonencode([
        merge(local.bq_field.req_string, { name = "case_id" }),
        merge(local.bq_field.string, { name = "first_activity" }),
        merge(local.bq_field.string, { name = "last_activity" }),
        merge(local.bq_field.integer, { name = "event_count" }),
        merge(local.bq_field.ts, { name = "start_time" }),
        merge(local.bq_field.ts, { name = "end_time" }),
        merge(local.bq_field.float, { name = "cycle_time_hours" }),
        merge(local.bq_field.string, { name = "supplier_id" }),
        merge(local.bq_field.string, { name = "category" }),
        merge(local.bq_field.string, { name = "variant" }),
        merge(local.bq_field.integer, { name = "variant_frequency" }),
      ])
      time_partitioning = { field = "start_time" }
      clustering        = ["supplier_id", "category"]
    }

    suppliers = {
      description = "Supplier reference data."
      schema = jsonencode([
        merge(local.bq_field.req_string, { name = "supplier_id" }),
        merge(local.bq_field.string, { name = "supplier_name" }),
        merge(local.bq_field.string, { name = "region" }),
        merge(local.bq_field.string, { name = "supplier_tier" }),
      ])
    }

    sla_rules = {
      description = "Target cycle time per category."
      schema = jsonencode([
        merge(local.bq_field.req_string, { name = "category" }),
        merge(local.bq_field.req_float, { name = "target_hours" }),
      ])
    }

    sla_predictions = {
      description = "SLA breach predictions per case. case_id is a logical foreign key to process_cases."
      schema = jsonencode([
        merge(local.bq_field.req_string, { name = "case_id" }),
        merge(local.bq_field.req_ts, { name = "predicted_at" }),
        merge(local.bq_field.req_float, { name = "breach_probability" }),
        merge(local.bq_field.req_string, { name = "risk_level" }),
        merge(local.bq_field.json, { name = "top_factors" }),
      ])
      time_partitioning = { field = "predicted_at" }
    }

    pipeline_runs = {
      description = "One row per pipeline execution."
      schema = jsonencode([
        merge(local.bq_field.integer, { name = "run_id" }),
        merge(local.bq_field.req_ts, { name = "started_at" }),
        merge(local.bq_field.ts, { name = "finished_at" }),
        merge(local.bq_field.req_string, { name = "status" }),
        merge(local.bq_field.req_string, { name = "source_path" }),
        merge(local.bq_field.integer, { name = "raw_row_count" }),
        merge(local.bq_field.integer, { name = "cleaned_row_count" }),
        merge(local.bq_field.integer, { name = "case_count" }),
        merge(local.bq_field.string, { name = "error_message" }),
      ])
      time_partitioning = { field = "started_at" }
    }
  }
}

module "bq_staging" {
  source = "../../modules/bigquery_dataset"

  project_id  = var.project_id
  dataset_id  = local.bq_staging_dataset
  location    = var.bigquery_location
  description = "Cleaned event log staged by the pipeline."
  tables      = local.bq_tables_staging

  # Dev environment: `terraform destroy` must leave nothing behind.
  delete_contents_on_destroy = true
  deletion_protection        = false

  editor_members = [local.runtime.pipeline]

  depends_on = [module.sa_runtime]
}

module "bq_analytics" {
  source = "../../modules/bigquery_dataset"

  project_id  = var.project_id
  dataset_id  = local.bq_analytics_dataset
  location    = var.bigquery_location
  description = "Analytics tables served by the operations-performance API."
  tables      = local.bq_tables_analytics

  delete_contents_on_destroy = true
  deletion_protection        = false

  # The API only reads; the pipeline writes.
  viewer_members = [local.runtime.performance]
  editor_members = [local.runtime.pipeline]

  depends_on = [module.sa_runtime]
}
