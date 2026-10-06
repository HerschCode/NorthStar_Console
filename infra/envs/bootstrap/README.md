# envs/bootstrap: the identity plane (human-applied)

Everything that decides **who may act, and what may alert you**, kept apart from the workloads and
applied only by a person from a workstation:

| Creates | Why it is here and not in `envs/dev` |
|---|---|
| API enablement | The CI apply identity has no `serviceusage.services.enable`, so a pipeline can never switch APIs on or off. |
| The billing budget (alerts at 100 / 400 / 800) | A pipeline that could edit the budget could silence the alarm that tells you it misbehaves. Applied first, before anything that costs money. |
| Workload identity pool and GitHub provider | Which repositories may authenticate at all cannot be changed by the pipeline. |
| The five GitHub identities and their roles | The pipeline cannot change its own permissions. |
| Data Access audit logging for token exchange, impersonation and secrets | Detective controls the pipeline cannot turn off. |
| State-bucket access for the plan/apply identities | Only the dev state bucket. This root's own state lives in a different bucket they cannot reach. |

State: `gs://<project>-tfstate-bootstrap`, prefix `northstar/bootstrap`. See
[`docs/runbook.md`](../../docs/runbook.md) for the order of operations and
[`docs/cloud-security.md`](../../docs/cloud-security.md) for the identity map.

```bash
terraform init -backend-config="bucket=<project>-tfstate-bootstrap" -backend-config="prefix=northstar/bootstrap"
terraform apply -var-file=terraform.tfvars
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
| [google_project_iam_audit_config.identity_plane](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/project_iam_audit_config) | resource |
| [google_project_iam_member.terraform_apply](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/project_iam_member) | resource |
| [google_project_iam_member.terraform_apply_project_iam_admin](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/project_iam_member) | resource |
| [google_project_service.apis](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/project_service) | resource |
| [google_storage_bucket_iam_member.dev_state_read](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/storage_bucket_iam_member) | resource |
| [google_storage_bucket_iam_member.dev_state_write](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/storage_bucket_iam_member) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_billing_account_id"></a> [billing\_account\_id](#input\_billing\_account\_id) | Billing account that pays for the project (gcloud billing accounts list). | `string` | n/a | yes |
| <a name="input_dev_state_bucket"></a> [dev\_state\_bucket](#input\_dev\_state\_bucket) | Name of the GCS bucket holding the dev root's state (created by hand, see docs/runbook.md). The plan identity gets read access and the apply identity read/write. It is a different bucket from the one holding this root's state. | `string` | n/a | yes |
| <a name="input_github_owner"></a> [github\_owner](#input\_github\_owner) | GitHub user or organisation that owns the repositories. Tokens from any other owner are rejected. | `string` | n/a | yes |
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | GCP project ID. The project and its billing link are created by hand before the first apply (see docs/runbook.md). | `string` | n/a | yes |
| <a name="input_project_number"></a> [project\_number](#input\_project\_number) | Numeric project number: gcloud projects describe <project-id> --format='value(projectNumber)'. | `string` | n/a | yes |
| <a name="input_alert_amounts"></a> [alert\_amounts](#input\_alert\_amounts) | Spend levels that trigger an email, ascending, in currency\_code. | `list(number)` | ```[ 100, 400, 800 ]``` | no |
| <a name="input_budget_amount"></a> [budget\_amount](#input\_budget\_amount) | Monthly budget in whole units of currency\_code. | `number` | `800` | no |
| <a name="input_currency_code"></a> [currency\_code](#input\_currency\_code) | Currency of the billing account. The Budget API rejects a budget in any other currency. | `string` | `"INR"` | no |
| <a name="input_deploy_ref"></a> [deploy\_ref](#input\_deploy\_ref) | Git ref the app deploy identities are pinned to. Only workflows running on this ref can deploy. | `string` | `"refs/heads/main"` | no |
| <a name="input_environment"></a> [environment](#input\_environment) | Environment label. | `string` | `"dev"` | no |
| <a name="input_github_repos"></a> [github\_repos](#input\_github\_repos) | Repository names (without the owner) of the infrastructure repo and the three apps. | ```object({ infra = string gateway = string assistant = string performance = string })``` | ```{ "assistant": "operations-assistant", "gateway": "llm-security-gateway", "infra": "northstar-infra", "performance": "operations-performance" }``` | no |
| <a name="input_region"></a> [region](#input\_region) | Default region. us-central1 is a free-tier region for Cloud Run, Artifact Registry and Cloud Storage. | `string` | `"us-central1"` | no |
| <a name="input_terraform_apply_environment"></a> [terraform\_apply\_environment](#input\_terraform\_apply\_environment) | GitHub environment that gates `terraform apply`. Configure it with required reviewers and a main-only deployment branch rule; the apply identity can only be impersonated by jobs running in it. | `string` | `"dev-apply"` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_budget"></a> [budget](#output\_budget) | The budget that watches this project and its alert levels. |
| <a name="output_ci_service_accounts"></a> [ci\_service\_accounts](#output\_ci\_service\_accounts) | Emails of the pipeline identities. |
| <a name="output_github_repository_variables"></a> [github\_repository\_variables](#output\_github\_repository\_variables) | Actions *variables* (not secrets: none of these is sensitive) to create on each repository. See docs/runbook.md. |
| <a name="output_workload_identity_provider"></a> [workload\_identity\_provider](#output\_workload\_identity\_provider) | Value of the `workload_identity_provider` input for google-github-actions/auth. |
<!-- END_TF_DOCS -->
