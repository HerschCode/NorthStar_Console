# naming

The single source of truth for every name that more than one root module has to agree on: Cloud
Run service names, service account IDs, and the values derived from them (emails, IAM member
strings, Cloud Run URLs). It creates no resources.

## Why it exists

`envs/bootstrap` (identities, budget) and `envs/dev` (workloads) are applied separately, by
different principals, into different state buckets. They cannot read each other's state, and
should not. But they must agree that the gateway's deploy identity is
`gh-deploy-gateway@<project>.iam.gserviceaccount.com`. A service account email is a pure function
of its ID and the project, and a Cloud Run URL is a pure function of the service name, project
number and region, so both roots derive them from this module instead of sharing state or
copy-pasting strings that can drift.

Because every value is derived from inputs, all of them are **known at plan time**. That is what
lets the policy checks see who each IAM grant is for on a first plan, before anything exists.

The URLs have a second job: they are the **audience** of the Google ID token that one service
presents to another (`AUTH_MODE=google_id_token` in the application repositories, see
[docs/app-integration.md](../../docs/app-integration.md)), so `envs/dev` hands exactly these values
to the callers as `OPS_ASSISTANT_URL` and `OPS_PERFORMANCE_API_URL`.

## Usage

```hcl
module "naming" {
  source = "../../modules/naming"

  project_id     = var.project_id
  project_number = var.project_number
  region         = var.region
}

# module.naming.service_urls["assistant"]                 -> https://operations-assistant-<number>.<region>.run.app
# module.naming.runtime_members["gateway"]                -> serviceAccount:gateway-sa@<project>.iam.gserviceaccount.com
# module.naming.ci_service_account_emails["terraform_apply"]
```

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.11.0 |





## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | Project ID. Used to derive service account emails. | `string` | n/a | yes |
| <a name="input_project_number"></a> [project\_number](#input\_project\_number) | Numeric project number. Used to derive Cloud Run's deterministic service URLs. | `string` | n/a | yes |
| <a name="input_region"></a> [region](#input\_region) | Cloud Run region. Used to derive service URLs. | `string` | n/a | yes |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_ci_members"></a> [ci\_members](#output\_ci\_members) | IAM member strings of the pipeline identities. |
| <a name="output_ci_service_account_emails"></a> [ci\_service\_account\_emails](#output\_ci\_service\_account\_emails) | Emails of the pipeline identities. |
| <a name="output_ci_service_account_ids"></a> [ci\_service\_account\_ids](#output\_ci\_service\_account\_ids) | Account IDs of the pipeline identities. |
| <a name="output_job"></a> [job](#output\_job) | Cloud Run job name for the data pipeline. |
| <a name="output_runtime_members"></a> [runtime\_members](#output\_runtime\_members) | IAM member strings of the runtime identities. |
| <a name="output_runtime_service_account_emails"></a> [runtime\_service\_account\_emails](#output\_runtime\_service\_account\_emails) | Emails of the runtime identities. |
| <a name="output_runtime_service_account_ids"></a> [runtime\_service\_account\_ids](#output\_runtime\_service\_account\_ids) | Account IDs of the runtime identities. |
| <a name="output_service_hosts"></a> [service\_hosts](#output\_service\_hosts) | Deterministic Cloud Run hostnames (<service>-<project number>.<region>.run.app), known before the services exist. |
| <a name="output_service_urls"></a> [service\_urls](#output\_service\_urls) | Deterministic Cloud Run URLs. Also the audience of the ID token a caller must present. |
| <a name="output_services"></a> [services](#output\_services) | Cloud Run service names keyed by role (gateway, assistant, performance). |
<!-- END_TF_DOCS -->
