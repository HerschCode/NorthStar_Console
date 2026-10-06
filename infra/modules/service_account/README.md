# service_account

A service account, plus the few grants that belong to the account itself. Everything else it may
touch is granted **on the resource it protects** (the secret, the dataset, the Cloud Run service),
never here and never on the project.

## Design decisions

* **No primitive roles, by construction.** `project_roles` rejects `roles/owner`, `roles/editor`
  and `roles/viewer` in a variable validation, so the mistake fails at `terraform validate` before
  any policy engine sees it. The conftest rules (IAM-001 and friends) are the independent second
  line.
* **Project roles are the exception.** `project_roles` exists only for permissions Google defines
  at project scope (for example `bigquery.jobs.create`, which is why the runtime identities of the
  API and the pipeline hold `roles/bigquery.jobUser`).
* **`act_as` lives on the service account.** `service_account_user_members` grants
  `roles/iam.serviceAccountUser` on *this* account only, so a deployer can attach one identity to
  a revision and not every identity in the project.
* **Identifiers are derived, not read.** `email`, `member` and `name` are computed from
  `account_id` and `project_id` rather than taken off the resource, so they are known at plan time.
  Each output carries a `depends_on` on the resource so consumers still wait for it to exist.

## Usage

```hcl
module "sa_perf" {
  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = "perf-sa"
  display_name = "Operations performance API (runtime)"

  project_roles                = ["roles/bigquery.jobUser"]
  service_account_user_members = ["serviceAccount:gh-deploy-perf@my-project.iam.gserviceaccount.com"]
}

# module.sa_perf.email  module.sa_perf.member  module.sa_perf.name
```

> Service account creation is eventually consistent. If a first apply fails with "service account
> does not exist" on a binding, simply apply again.

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
| [google_project_iam_member.project_roles](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/project_iam_member) | resource |
| [google_service_account.this](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/service_account) | resource |
| [google_service_account_iam_binding.service_account_user](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/service_account_iam_binding) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_account_id"></a> [account\_id](#input\_account\_id) | Service account ID (the part before the @): 6-30 characters, lowercase letters, digits and hyphens. | `string` | n/a | yes |
| <a name="input_display_name"></a> [display\_name](#input\_display\_name) | Human-readable name shown in the console. | `string` | n/a | yes |
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | Project that owns the service account. | `string` | n/a | yes |
| <a name="input_description"></a> [description](#input\_description) | What this identity is for and what it may touch (max 256 bytes). | `string` | `""` | no |
| <a name="input_project_roles"></a> [project\_roles](#input\_project\_roles) | Roles granted on the whole project. Only for permissions GCP defines at project scope (for example bigquery.jobs.create). Anything that can be granted on a single resource (secret, dataset, Cloud Run service, repository) must be granted on that resource instead. Primitive roles (owner, editor, viewer) are rejected. | `set(string)` | `[]` | no |
| <a name="input_service_account_user_members"></a> [service\_account\_user\_members](#input\_service\_account\_user\_members) | Principals allowed to attach or act as this service account (roles/iam.serviceAccountUser on this service account only, never on the project). Deployers need this to create Cloud Run revisions that run as it. | `set(string)` | `[]` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_account_id"></a> [account\_id](#output\_account\_id) | The account ID (the part of the email before the @). |
| <a name="output_email"></a> [email](#output\_email) | Service account email. Derived from the inputs, so it is known at plan time. |
| <a name="output_member"></a> [member](#output\_member) | IAM member string (serviceAccount:<email>) for use in bindings. |
| <a name="output_name"></a> [name](#output\_name) | Fully-qualified resource name (projects/<project>/serviceAccounts/<email>), as expected by google\_service\_account\_iam\_* resources. |
<!-- END_TF_DOCS -->
