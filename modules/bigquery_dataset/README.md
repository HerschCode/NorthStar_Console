# bigquery_dataset

A BigQuery dataset with **partitioned, clustered** tables and dataset-scoped access.

## Design decisions

* **Partitioning and clustering are the cost control.** BigQuery bills by bytes scanned and has no
  indexes; a query that filters on a partitioned, clustered column reads only the relevant blocks.
  The module validates that every partition field and clustering column exists in the table's
  schema, so a typo fails at plan time instead of silently creating an unpartitioned table.
* **Terraform creates the empty shells first.** A `WRITE_TRUNCATE` load into a table that does not
  exist yet creates a plain, unpartitioned one, which is exactly the silent cost surprise the
  shells prevent. Loaders must append to (or truncate) the existing tables, not recreate them.
* **Dataset-level IAM only.** `viewer_members` and `editor_members` are authoritative bindings of
  `roles/bigquery.dataViewer` / `roles/bigquery.dataEditor` on this dataset. Running a query also
  needs `roles/bigquery.jobUser`, which Google defines at project scope, so it is granted by the
  service account module.
* **Protected by default.** `deletion_protection` is on and `delete_contents_on_destroy` is off.
  The dev environment flips both so `terraform destroy` leaves nothing billable behind; anything
  holding data worth keeping must not.

## Usage

```hcl
module "bq_analytics" {
  source = "../../modules/bigquery_dataset"

  project_id = var.project_id
  dataset_id = "operations_performance_analytics"

  tables = {
    process_cases = {
      schema            = jsonencode([{ name = "case_id", type = "STRING", mode = "REQUIRED" }, { name = "start_time", type = "TIMESTAMP", mode = "NULLABLE" }])
      time_partitioning = { field = "start_time" }
      clustering        = ["case_id"]
    }
  }

  viewer_members = [module.sa_perf.member]
  editor_members = [module.sa_pipeline.member]
}
```

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.11.0 |
| <a name="requirement_google"></a> [google](#requirement\_google) | ~> 8.4 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_google"></a> [google](#provider\_google) | ~> 8.4 |

## Resources

| Name | Type |
| ---- | ---- |
| [google_bigquery_dataset.this](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/bigquery_dataset) | resource |
| [google_bigquery_dataset_iam_binding.editors](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/bigquery_dataset_iam_binding) | resource |
| [google_bigquery_dataset_iam_binding.viewers](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/bigquery_dataset_iam_binding) | resource |
| [google_bigquery_table.this](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/bigquery_table) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_dataset_id"></a> [dataset\_id](#input\_dataset\_id) | Dataset ID: letters, digits and underscores. | `string` | n/a | yes |
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | Project that owns the dataset. | `string` | n/a | yes |
| <a name="input_default_table_expiration_ms"></a> [default\_table\_expiration\_ms](#input\_default\_table\_expiration\_ms) | Default lifetime of new tables in milliseconds (min 3600000). Null means tables never expire. | `number` | `null` | no |
| <a name="input_delete_contents_on_destroy"></a> [delete\_contents\_on\_destroy](#input\_delete\_contents\_on\_destroy) | Delete all tables when the dataset is destroyed. Needed for a clean `terraform destroy` of a dev environment; keep false anywhere the data matters. | `bool` | `false` | no |
| <a name="input_deletion_protection"></a> [deletion\_protection](#input\_deletion\_protection) | Refuse to destroy tables while true. | `bool` | `true` | no |
| <a name="input_description"></a> [description](#input\_description) | What the dataset holds. | `string` | `""` | no |
| <a name="input_editor_members"></a> [editor\_members](#input\_editor\_members) | Principals that may read and write the dataset's tables (roles/bigquery.dataEditor on this dataset only). Authoritative for that role. | `set(string)` | `[]` | no |
| <a name="input_labels"></a> [labels](#input\_labels) | Labels for the dataset. | `map(string)` | `{}` | no |
| <a name="input_location"></a> [location](#input\_location) | Dataset location. Cannot be changed later (it forces a new dataset). | `string` | `"US"` | no |
| <a name="input_tables"></a> [tables](#input\_tables) | Tables to create, keyed by table ID. `schema` is a BigQuery JSON schema string. Partitioning and clustering are what keep queries cheap (BigQuery bills by bytes scanned), so a table with a time column should declare both. Terraform creates the empty, correctly partitioned shells first; loaders must then append to them rather than recreate them. | ```map(object({ description = optional(string, "") schema = string time_partitioning = optional(object({ type = optional(string, "DAY") field = string expiration_ms = optional(number) })) clustering = optional(list(string), []) require_partition_filter = optional(bool, false) }))``` | `{}` | no |
| <a name="input_viewer_members"></a> [viewer\_members](#input\_viewer\_members) | Principals that may read the dataset's tables (roles/bigquery.dataViewer on this dataset only). Authoritative for that role. Running queries additionally needs roles/bigquery.jobUser on the project. | `set(string)` | `[]` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_dataset_id"></a> [dataset\_id](#output\_dataset\_id) | Dataset ID. |
| <a name="output_id"></a> [id](#output\_id) | Resource ID (projects/<project>/datasets/<dataset>). |
| <a name="output_table_ids"></a> [table\_ids](#output\_table\_ids) | Fully-qualified table IDs (<project>.<dataset>.<table>), keyed by table name. |
<!-- END_TF_DOCS -->
