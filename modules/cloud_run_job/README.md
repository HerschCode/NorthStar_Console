# cloud_run_job

A Cloud Run job with an optional Cloud Scheduler trigger.

## Design decisions

* **Google APIs need an OAuth token, not an OIDC one.** Cloud Scheduler starts a job by POSTing to
  the Cloud Run Admin API (`run.googleapis.com/.../jobs/<name>:run`). Calls to Google APIs use an
  OAuth access token; an OIDC token is only for calling your own Cloud Run *services*. The
  scheduler's service account holds `roles/run.invoker` on this one job and nothing else.
* **Guarded wiring.** A schedule without a service account, or with a service account that is not
  in `invoker_members`, is refused at plan time instead of failing with a 403 at 02:00.
* **Created paused.** `schedule_paused = true` until a real image is deployed, otherwise every tick
  would run the placeholder image and fail.
* **No public invokers.** `invoker_members` rejects `allUsers` and `allAuthenticatedUsers`.
* **Same ownership split as the service:** Terraform owns config and secrets, app CI owns the image.

## Usage

```hcl
module "run_pipeline" {
  source = "../../modules/cloud_run_job"

  project_id            = var.project_id
  region                = "us-central1"
  name                  = "operations-pipeline"
  image                 = "us-docker.pkg.dev/cloudrun/container/job" # placeholder
  service_account_email = module.sa_pipeline.email
  command               = ["python"]
  args                  = ["-m", "scripts.run_pipeline"]

  invoker_members                 = [module.sa_scheduler.member]
  scheduler_service_account_email = module.sa_scheduler.email
  schedule                        = "0 2 * * *"
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
| [google_cloud_run_v2_job.this](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/cloud_run_v2_job) | resource |
| [google_cloud_run_v2_job_iam_binding.developer](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/cloud_run_v2_job_iam_binding) | resource |
| [google_cloud_run_v2_job_iam_binding.invoker](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/cloud_run_v2_job_iam_binding) | resource |
| [google_cloud_scheduler_job.trigger](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/cloud_scheduler_job) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_image"></a> [image](#input\_image) | Image for the FIRST job definition only. After creation it is ignored by Terraform; the app repo's CI updates it with `gcloud run jobs deploy --image`. | `string` | n/a | yes |
| <a name="input_name"></a> [name](#input\_name) | Job name (max 49 characters: lowercase letters, digits, hyphens). | `string` | n/a | yes |
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | Project that owns the job. | `string` | n/a | yes |
| <a name="input_region"></a> [region](#input\_region) | Region to run the job in. | `string` | n/a | yes |
| <a name="input_service_account_email"></a> [service\_account\_email](#input\_service\_account\_email) | Runtime identity of the job. Always a dedicated service account, never the default compute account. | `string` | n/a | yes |
| <a name="input_args"></a> [args](#input\_args) | Arguments for the entrypoint. Null keeps the image's CMD. | `list(string)` | `null` | no |
| <a name="input_command"></a> [command](#input\_command) | Entrypoint override (not run in a shell). Null keeps the image's ENTRYPOINT. | `list(string)` | `null` | no |
| <a name="input_cpu"></a> [cpu](#input\_cpu) | CPU limit. Cloud Run's Terraform schema only accepts 1, 2, 4, 6 or 8. | `string` | `"1"` | no |
| <a name="input_deletion_protection"></a> [deletion\_protection](#input\_deletion\_protection) | Refuse to destroy the job while true. Off by default so dev environments tear down cleanly. | `bool` | `false` | no |
| <a name="input_deployer_members"></a> [deployer\_members](#input\_deployer\_members) | Principals allowed to update the job definition (roles/run.developer on this job only). | `set(string)` | `[]` | no |
| <a name="input_env"></a> [env](#input\_env) | Plain environment variables. Never put secrets here; use secret\_env. | `map(string)` | `{}` | no |
| <a name="input_invoker_members"></a> [invoker\_members](#input\_invoker\_members) | Principals allowed to start an execution (roles/run.invoker on this job only). Normally just the scheduler's service account. Public principals are never accepted. | `set(string)` | `[]` | no |
| <a name="input_labels"></a> [labels](#input\_labels) | Labels for the job. | `map(string)` | `{}` | no |
| <a name="input_max_retries"></a> [max\_retries](#input\_max\_retries) | Retries per task before the execution is marked failed. | `number` | `1` | no |
| <a name="input_memory"></a> [memory](#input\_memory) | Memory limit, e.g. 512Mi or 2Gi. | `string` | `"1Gi"` | no |
| <a name="input_schedule"></a> [schedule](#input\_schedule) | Cron expression for Cloud Scheduler. Null creates no scheduler job (run the job manually). | `string` | `null` | no |
| <a name="input_schedule_paused"></a> [schedule\_paused](#input\_schedule\_paused) | Create the schedule paused. Keep true until a real image has been deployed, otherwise every tick runs the placeholder image and fails. | `bool` | `true` | no |
| <a name="input_schedule_time_zone"></a> [schedule\_time\_zone](#input\_schedule\_time\_zone) | Time zone the schedule is interpreted in. | `string` | `"Etc/UTC"` | no |
| <a name="input_scheduler_region"></a> [scheduler\_region](#input\_scheduler\_region) | Region of the Cloud Scheduler job. Null uses the job's own region. | `string` | `null` | no |
| <a name="input_scheduler_service_account_email"></a> [scheduler\_service\_account\_email](#input\_scheduler\_service\_account\_email) | Service account Cloud Scheduler uses to call the Cloud Run Admin API. Must be one of invoker\_members. Required when schedule is set. | `string` | `null` | no |
| <a name="input_secret_env"></a> [secret\_env](#input\_secret\_env) | Environment variables sourced from Secret Manager: name => { secret\_id, version }. The runtime service account must be the secret's accessor. | ```map(object({ secret_id = string version = optional(string, "latest") }))``` | `{}` | no |
| <a name="input_task_count"></a> [task\_count](#input\_task\_count) | Number of tasks per execution. | `number` | `1` | no |
| <a name="input_timeout_seconds"></a> [timeout\_seconds](#input\_timeout\_seconds) | Maximum run time of one task attempt. | `number` | `1800` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_id"></a> [id](#output\_id) | Resource ID (projects/<project>/locations/<region>/jobs/<name>). |
| <a name="output_name"></a> [name](#output\_name) | Job name. |
| <a name="output_run_uri"></a> [run\_uri](#output\_run\_uri) | The Cloud Run Admin API endpoint that starts an execution (what Cloud Scheduler POSTs to). |
| <a name="output_scheduler_job_name"></a> [scheduler\_job\_name](#output\_scheduler\_job\_name) | Name of the Cloud Scheduler job, or null when the job is unscheduled. |
<!-- END_TF_DOCS -->
