# cloud_run_service

A Cloud Run service that is **private unless two separate keys are turned**.

## Design decisions

* **Private by default; public needs two keys.** `invoker_members` may contain `allUsers` only if
  `allow_public = true` as well, otherwise a plan-time precondition refuses it.
  `allAuthenticatedUsers` (every Google account) is never accepted. Only the gateway sets both keys.
  The conftest rule RUN-001 is the independent second line: it fails the plan if any other service
  ends up public through some other route.
* **Authoritative invoker binding.** Who may call the service is a `google_cloud_run_v2_service_iam_binding`,
  so a caller added out of band is drift and is removed on the next apply.
* **Identity is mandatory and dedicated.** `service_account_email` must be a user-managed service
  account and may not be the default compute account.
* **Terraform owns configuration; app CI owns the image.** The image is `ignore_changes`, along
  with the labels and metadata `gcloud run deploy` stamps. Env vars, secrets, scaling and identity
  stay Terraform's, so an app deployment must use `--image` only. `image` here is just the
  placeholder for the first revision.
* **Cost ceilings.** Scale-to-zero, request-based CPU billing and a small `max_instances` keep it
  inside the free tier and cap what a flood can cost.
* **Ingress is `INGRESS_TRAFFIC_ALL`.** Internal-only ingress would need a VPC and NAT (paid).
  Private services are still unreachable in practice: the IAM check answers 403 to any caller
  without a valid token from an allowed principal.
* **Probes.** The default is Cloud Run's TCP startup check. Set `startup_probe_path` once a real
  image (with a `/health`) is deployed.

## Usage

```hcl
module "run_assistant" {
  source = "../../modules/cloud_run_service"

  project_id            = var.project_id
  region                = "us-central1"
  name                  = "operations-assistant"
  image                 = "us-docker.pkg.dev/cloudrun/container/hello" # placeholder
  service_account_email = module.sa_assistant.email

  env        = { AGENT_PROVIDER = "groq" }
  secret_env = { GROQ_API_KEY = { secret_id = "groq-api-key" } }

  invoker_members  = [module.sa_gateway.member] # only the gateway may call it
  deployer_members = [module.sa_deploy_assistant.member]
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
| [google_cloud_run_v2_service.this](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/cloud_run_v2_service) | resource |
| [google_cloud_run_v2_service_iam_binding.developer](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/cloud_run_v2_service_iam_binding) | resource |
| [google_cloud_run_v2_service_iam_binding.invoker](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/cloud_run_v2_service_iam_binding) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_image"></a> [image](#input\_image) | Image for the FIRST revision only (a public hello-world placeholder until the app repo's CI ships a real one). After creation the image is ignored by Terraform (lifecycle.ignore\_changes), so app deployments never fight infrastructure applies. Config (env, secrets, scaling, identity) stays owned by Terraform; app CI must deploy with `--image` only. | `string` | n/a | yes |
| <a name="input_invoker_members"></a> [invoker\_members](#input\_invoker\_members) | Principals allowed to call the service (roles/run.invoker). Authoritative: anyone added out of band is removed on the next apply. `allUsers` is only accepted together with allow\_public = true. `allAuthenticatedUsers` is never accepted (any Google account is effectively public). | `set(string)` | n/a | yes |
| <a name="input_name"></a> [name](#input\_name) | Service name (max 49 characters: lowercase letters, digits, hyphens). | `string` | n/a | yes |
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | Project that owns the service. | `string` | n/a | yes |
| <a name="input_region"></a> [region](#input\_region) | Region to deploy to. | `string` | n/a | yes |
| <a name="input_service_account_email"></a> [service\_account\_email](#input\_service\_account\_email) | Runtime identity. Always a dedicated service account, never the default compute account. | `string` | n/a | yes |
| <a name="input_allow_public"></a> [allow\_public](#input\_allow\_public) | Second key for public exposure: must be true for `allUsers` to be accepted in invoker\_members. Only the gateway sets this. | `bool` | `false` | no |
| <a name="input_container_port"></a> [container\_port](#input\_container\_port) | Port the container listens on. Cloud Run also injects it as $PORT; all three apps already honour that. | `number` | `8080` | no |
| <a name="input_cpu"></a> [cpu](#input\_cpu) | CPU limit. Cloud Run's Terraform schema only accepts 1, 2, 4, 6 or 8. | `string` | `"1"` | no |
| <a name="input_deletion_protection"></a> [deletion\_protection](#input\_deletion\_protection) | Refuse to destroy the service while true. Off by default so dev environments tear down cleanly. | `bool` | `false` | no |
| <a name="input_deployer_members"></a> [deployer\_members](#input\_deployer\_members) | Principals allowed to deploy new revisions (roles/run.developer on this service only). | `set(string)` | `[]` | no |
| <a name="input_description"></a> [description](#input\_description) | What the service does. | `string` | `""` | no |
| <a name="input_env"></a> [env](#input\_env) | Plain environment variables. Never put secrets here; use secret\_env. | `map(string)` | `{}` | no |
| <a name="input_ingress"></a> [ingress](#input\_ingress) | Ingress setting. INGRESS\_TRAFFIC\_ALL is required here because there is no load balancer or VPC egress path (both cost money); the private services are still protected by the IAM invoker check, which answers 403 to any caller without a valid token from an allowed principal. | `string` | `"INGRESS_TRAFFIC_ALL"` | no |
| <a name="input_labels"></a> [labels](#input\_labels) | Labels for the service. | `map(string)` | `{}` | no |
| <a name="input_max_concurrency"></a> [max\_concurrency](#input\_max\_concurrency) | Concurrent requests per instance. | `number` | `80` | no |
| <a name="input_max_instances"></a> [max\_instances](#input\_max\_instances) | Maximum instances. Also the ceiling on cost if something floods the service. | `number` | `2` | no |
| <a name="input_memory"></a> [memory](#input\_memory) | Memory limit, e.g. 512Mi or 2Gi. | `string` | `"512Mi"` | no |
| <a name="input_min_instances"></a> [min\_instances](#input\_min\_instances) | Minimum instances. Keep at 0 (scale to zero) to stay inside the free tier. | `number` | `0` | no |
| <a name="input_secret_env"></a> [secret\_env](#input\_secret\_env) | Environment variables sourced from Secret Manager: name => { secret\_id, version }. The runtime service account must be the secret's accessor. | ```map(object({ secret_id = string version = optional(string, "latest") }))``` | `{}` | no |
| <a name="input_startup_probe_path"></a> [startup\_probe\_path](#input\_startup\_probe\_path) | HTTP path for the startup probe. Null keeps Cloud Run's default TCP probe (needed while the placeholder image, which has no /health, is deployed). | `string` | `null` | no |
| <a name="input_timeout_seconds"></a> [timeout\_seconds](#input\_timeout\_seconds) | Per-request timeout (1-3600). The gateway waits on an LLM-backed agent, so it needs more than the 300 s default of some stacks. | `number` | `300` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_id"></a> [id](#output\_id) | Resource ID (projects/<project>/locations/<region>/services/<name>). |
| <a name="output_invoker_members"></a> [invoker\_members](#output\_invoker\_members) | Principals allowed to call the service. |
| <a name="output_name"></a> [name](#output\_name) | Service name. |
| <a name="output_service_account_email"></a> [service\_account\_email](#output\_service\_account\_email) | Runtime identity of the service. |
| <a name="output_uri"></a> [uri](#output\_uri) | Default HTTPS URL of the service (known after apply). |
<!-- END_TF_DOCS -->
