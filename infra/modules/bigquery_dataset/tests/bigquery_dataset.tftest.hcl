mock_provider "google" {}

variables {
  project_id = "northstar-test"
  dataset_id = "operations_performance_analytics"

  tables = {
    process_cases = {
      description = "One row per case"
      schema = jsonencode([
        { name = "case_id", type = "STRING", mode = "REQUIRED" },
        { name = "start_time", type = "TIMESTAMP", mode = "NULLABLE" },
        { name = "supplier_id", type = "STRING", mode = "NULLABLE" },
        { name = "category", type = "STRING", mode = "NULLABLE" },
      ])
      time_partitioning = { field = "start_time" }
      clustering        = ["supplier_id", "category"]
    }
    suppliers = {
      schema = jsonencode([
        { name = "supplier_id", type = "STRING", mode = "REQUIRED" },
      ])
    }
  }
}

run "dataset_defaults_are_conservative" {
  command = plan

  assert {
    condition     = google_bigquery_dataset.this.delete_contents_on_destroy == false
    error_message = "the module must not delete data on destroy unless asked"
  }

  assert {
    condition     = google_bigquery_dataset.this.location == "US"
    error_message = "default location is US"
  }
}

run "tables_are_partitioned_and_clustered" {
  command = plan

  assert {
    condition     = google_bigquery_table.this["process_cases"].time_partitioning[0].field == "start_time"
    error_message = "process_cases must be partitioned on start_time"
  }

  assert {
    condition     = google_bigquery_table.this["process_cases"].time_partitioning[0].type == "DAY"
    error_message = "daily partitions by default"
  }

  assert {
    condition     = google_bigquery_table.this["process_cases"].clustering == tolist(["supplier_id", "category"])
    error_message = "clustering columns must be passed through in order"
  }

  assert {
    condition     = length(google_bigquery_table.this["suppliers"].time_partitioning) == 0
    error_message = "a table without a partition spec must stay unpartitioned"
  }
}

run "tables_are_protected_by_default" {
  command = plan

  assert {
    condition     = google_bigquery_table.this["suppliers"].deletion_protection == true
    error_message = "table deletion protection must default to on"
  }
}

run "table_ids_are_fully_qualified" {
  command = plan

  assert {
    condition     = output.table_ids["suppliers"] == "northstar-test.operations_performance_analytics.suppliers"
    error_message = "table_ids must be <project>.<dataset>.<table>"
  }
}

run "no_iam_bindings_unless_requested" {
  command = plan

  assert {
    condition     = length(google_bigquery_dataset_iam_binding.viewers) == 0 && length(google_bigquery_dataset_iam_binding.editors) == 0
    error_message = "nobody gets access unless listed"
  }
}

run "dataset_scoped_bindings" {
  command = plan

  variables {
    viewer_members = ["serviceAccount:perf-sa@northstar-test.iam.gserviceaccount.com"]
    editor_members = ["serviceAccount:pipeline-sa@northstar-test.iam.gserviceaccount.com"]
  }

  assert {
    condition     = google_bigquery_dataset_iam_binding.viewers[0].role == "roles/bigquery.dataViewer"
    error_message = "viewers get dataViewer"
  }

  assert {
    condition     = google_bigquery_dataset_iam_binding.editors[0].role == "roles/bigquery.dataEditor"
    error_message = "editors get dataEditor"
  }

  assert {
    condition     = google_bigquery_dataset_iam_binding.viewers[0].dataset_id == "operations_performance_analytics"
    error_message = "the binding must target this dataset, not the project"
  }
}

run "rejects_partition_field_missing_from_schema" {
  command = plan

  variables {
    tables = {
      bad = {
        schema            = jsonencode([{ name = "a", type = "STRING", mode = "NULLABLE" }])
        time_partitioning = { field = "not_a_column" }
      }
    }
  }

  expect_failures = [var.tables]
}

run "rejects_clustering_column_missing_from_schema" {
  command = plan

  variables {
    tables = {
      bad = {
        schema     = jsonencode([{ name = "a", type = "STRING", mode = "NULLABLE" }])
        clustering = ["b"]
      }
    }
  }

  expect_failures = [var.tables]
}

run "rejects_invalid_schema_json" {
  command = plan

  variables {
    tables = {
      bad = { schema = "{not json" }
    }
  }

  expect_failures = [var.tables]
}
