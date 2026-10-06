# envs/dev: the workloads

The three Cloud Run services, the pipeline job, and everything they use. Applied by a person the
first time and afterwards by CI (`plan` on pull requests, `apply` by manual dispatch inside a
protected GitHub environment).

```text
Internet ──► llm-security-gateway  (public: the only ingress)
                 │  Google ID token, as gateway-sa
                 ▼
             operations-assistant   (private: invoker = gateway-sa)
                 │  Google ID token, as assistant-sa
                 ▼
             operations-performance (private: invoker = assistant-sa)
                 │                     │
          Neon Postgres           BigQuery (perf-sa: dataViewer + jobUser)

Cloud Scheduler ──► Cloud Run job: pipeline  (pipeline-sa: dataEditor + jobUser)
```

* Terraform owns the configuration (identity, env vars, secrets, scaling); each app repo's CI owns
  the **image** only, so deployments never fight applies.
* Services start on a public hello-world placeholder image and the job's schedule starts paused,
  so the very first apply succeeds before any app has shipped.
* Secrets are created empty (plus a placeholder version); real values are added with `gcloud` and
  never enter state.
* Requires `envs/bootstrap` to have been applied first: it enables the APIs and creates the
  GitHub identities that this root grants access to.

State: `gs://<project>-tfstate-dev`, prefix `northstar/dev`.

```bash
terraform init -backend-config="bucket=<project>-tfstate-dev" -backend-config="prefix=northstar/dev"
terraform plan  -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.11.0 |
| <a name="requirement_google"></a> [google](#requirement\_google) | ~> 8.4 |





## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_neon_host"></a> [neon\_host](#input\_neon\_host) | Hostname of the Neon Postgres endpoint. Not a secret; the two role passwords are, and live in Secret Manager. | `string` | n/a | yes |
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | GCP project ID (the same project the bootstrap root was applied to). | `string` | n/a | yes |
| <a name="input_project_number"></a> [project\_number](#input\_project\_number) | Numeric project number: gcloud projects describe <project-id> --format='value(projectNumber)'. Cloud Run's deterministic URLs are built from it. | `string` | n/a | yes |
| <a name="input_agent_model"></a> [agent\_model](#input\_agent\_model) | Model name passed to the provider. | `string` | `"openai/gpt-oss-120b"` | no |
| <a name="input_agent_provider"></a> [agent\_provider](#input\_agent\_provider) | LLM provider the assistant uses (anthropic, groq or gemini). Must match a key in llm\_secrets. | `string` | `"groq"` | no |
| <a name="input_alert_emails"></a> [alert\_emails](#input\_alert\_emails) | Addresses that receive uptime, 5xx and block-rate alerts. | `list(string)` | `[]` | no |
| <a name="input_assistant_memory"></a> [assistant\_memory](#input\_assistant\_memory) | Assistant memory (Chroma vector store and embeddings). | `string` | `"2Gi"` | no |
| <a name="input_bigquery_location"></a> [bigquery\_location](#input\_bigquery\_location) | BigQuery dataset location. The US multi-region is fine next to us-central1. | `string` | `"US"` | no |
| <a name="input_block_spike_threshold"></a> [block\_spike\_threshold](#input\_block\_spike\_threshold) | Gateway blocks within five minutes that trigger an alert. | `number` | `20` | no |
| <a name="input_environment"></a> [environment](#input\_environment) | Environment label. | `string` | `"dev"` | no |
| <a name="input_five_xx_threshold"></a> [five\_xx\_threshold](#input\_five\_xx\_threshold) | 5xx responses within five minutes that trigger an alert. | `number` | `5` | no |
| <a name="input_gateway_memory"></a> [gateway\_memory](#input\_gateway\_memory) | Gateway memory. It serves three detection layers (rules, TF-IDF similarity and a numpy classifier), no torch. | `string` | `"1Gi"` | no |
| <a name="input_llm_secrets"></a> [llm\_secrets](#input\_llm\_secrets) | LLM API keys the assistant may use: Secret Manager secret ID => environment variable it is exposed as. Each secret is readable only by the assistant's service account. Terraform creates the empty container plus a placeholder version; add the real key with `gcloud secrets versions add <id> --data-file=-` so it never enters Terraform state. | `map(string)` | ```{ "groq-api-key": "GROQ_API_KEY" }``` | no |
| <a name="input_max_instances"></a> [max\_instances](#input\_max\_instances) | Maximum instances per service. Also the ceiling on what a traffic flood can cost. | `number` | `2` | no |
| <a name="input_neon_api_user"></a> [neon\_api\_user](#input\_neon\_api\_user) | Read-only database role used by the API (ops\_api\_reader in operations-performance's sql/schema/004\_create\_roles.sql). | `string` | `"ops_api_reader"` | no |
| <a name="input_neon_database"></a> [neon\_database](#input\_neon\_database) | Database name. | `string` | `"operations_performance"` | no |
| <a name="input_neon_pipeline_user"></a> [neon\_pipeline\_user](#input\_neon\_pipeline\_user) | Read/write database role used by the pipeline job (ops\_pipeline\_writer). | `string` | `"ops_pipeline_writer"` | no |
| <a name="input_performance_memory"></a> [performance\_memory](#input\_performance\_memory) | Performance API memory. | `string` | `"1Gi"` | no |
| <a name="input_pipeline_args"></a> [pipeline\_args](#input\_pipeline\_args) | Arguments of the pipeline job. To also load BigQuery, chain the migration in the image or add a second job. | `list(string)` | ```[ "-m", "scripts.run_pipeline" ]``` | no |
| <a name="input_pipeline_command"></a> [pipeline\_command](#input\_pipeline\_command) | Entrypoint of the pipeline job (the image's own ENTRYPOINT is empty). | `list(string)` | ```[ "python" ]``` | no |
| <a name="input_pipeline_memory"></a> [pipeline\_memory](#input\_pipeline\_memory) | Pipeline job memory. | `string` | `"1Gi"` | no |
| <a name="input_pipeline_schedule"></a> [pipeline\_schedule](#input\_pipeline\_schedule) | Cron schedule for the pipeline job. | `string` | `"0 2 * * *"` | no |
| <a name="input_pipeline_schedule_paused"></a> [pipeline\_schedule\_paused](#input\_pipeline\_schedule\_paused) | Create the schedule paused. Unpause once a real pipeline image has been deployed. | `bool` | `true` | no |
| <a name="input_placeholder_image"></a> [placeholder\_image](#input\_placeholder\_image) | Image the three services start with until each app repo's CI ships a real one. Terraform ignores the image afterwards. | `string` | `"us-docker.pkg.dev/cloudrun/container/hello"` | no |
| <a name="input_placeholder_job_image"></a> [placeholder\_job\_image](#input\_placeholder\_job\_image) | Image the pipeline job starts with. A real one replaces it on the first deploy from operations-performance's CI. | `string` | `"us-docker.pkg.dev/cloudrun/container/job"` | no |
| <a name="input_region"></a> [region](#input\_region) | Region for Cloud Run, Artifact Registry, Secret Manager replicas and Cloud Scheduler. us-central1 is a free-tier region. | `string` | `"us-central1"` | no |
| <a name="input_runbook_url"></a> [runbook\_url](#input\_runbook\_url) | Link included in every alert. Null omits it. | `string` | `null` | no |
| <a name="input_uptime_period_seconds"></a> [uptime\_period\_seconds](#input\_uptime\_period\_seconds) | How often each uptime check runs (60, 300, 600 or 900). | `number` | `300` | no |
| <a name="input_use_http_startup_probes"></a> [use\_http\_startup\_probes](#input\_use\_http\_startup\_probes) | Probe GET /health on startup instead of Cloud Run's default TCP check. Leave false until real images are deployed: the placeholder image has no /health. | `bool` | `false` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_image_prefix"></a> [image\_prefix](#output\_image\_prefix) | Prefix for image names: <prefix>/<service>:<tag>. |
| <a name="output_images"></a> [images](#output\_images) | Full image names the app CI pipelines push to. |
| <a name="output_pipeline_job"></a> [pipeline\_job](#output\_pipeline\_job) | Cloud Run job that runs the data pipeline. |
| <a name="output_runtime_service_accounts"></a> [runtime\_service\_accounts](#output\_runtime\_service\_accounts) | Emails of the workload identities. |
| <a name="output_secrets"></a> [secrets](#output\_secrets) | Secrets to fill after the first apply, with the identity that reads each one. Add the real value with `gcloud secrets versions add <id> --data-file=-`. |
| <a name="output_service_urls"></a> [service\_urls](#output\_service\_urls) | Deterministic Cloud Run URLs. Only the gateway answers anonymous callers; the others return 403. |
<!-- END_TF_DOCS -->
