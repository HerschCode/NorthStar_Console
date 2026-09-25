# secret

A Secret Manager secret that is readable by **exactly one** service account.

## Design decisions

* **One reader, by construction.** The module takes a single `accessor_service_account_email`. If
  two workloads need the same value, create two secrets. The conftest rule SEC-001 enforces the same
  invariant across the whole plan, so it holds even for secrets created outside this module.
* **The value never enters Terraform.** The module creates the empty container and, by default, a
  placeholder version written through a *write-only* argument (`secret_data_wo`), so nothing is
  stored in state or shown in a plan. The real value is added with
  `gcloud secrets versions add <id> --data-file=-`. The placeholder exists only so a Cloud Run
  service that references `latest` can start on the first apply.
* **Authoritative IAM.** The accessor is set with `google_secret_manager_secret_iam_binding`, so a
  reader added out of band (console, gcloud) is drift and is removed on the next apply.
* **Single-region replication** (`replica_locations`) keeps the secret in one place and, because
  Secret Manager bills per active version per location, keeps it inside the free allowance of six
  versions.

## Usage

```hcl
module "secret" {
  source = "../../modules/secret"

  project_id                     = var.project_id
  secret_id                      = "groq-api-key"
  accessor_service_account_email = "assistant-sa@my-project.iam.gserviceaccount.com"
  replica_locations              = ["us-central1"]
}

# Then, once:  echo -n "$KEY" | gcloud secrets versions add groq-api-key --data-file=-
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
| [google_secret_manager_secret.this](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/secret_manager_secret) | resource |
| [google_secret_manager_secret_iam_binding.accessor](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/secret_manager_secret_iam_binding) | resource |
| [google_secret_manager_secret_version.placeholder](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/secret_manager_secret_version) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_accessor_service_account_email"></a> [accessor\_service\_account\_email](#input\_accessor\_service\_account\_email) | Email of the ONE service account allowed to read this secret's payload. The module takes a single value on purpose: if two workloads need the same value, create two secrets. That keeps every secret readable by exactly one identity, which the conftest policy also enforces. | `string` | n/a | yes |
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | Project that owns the secret. | `string` | n/a | yes |
| <a name="input_secret_id"></a> [secret\_id](#input\_secret\_id) | Secret ID, unique within the project (letters, digits, hyphens, underscores). | `string` | n/a | yes |
| <a name="input_create_placeholder_version"></a> [create\_placeholder\_version](#input\_create\_placeholder\_version) | Create version 1 with an obvious placeholder so services that reference `latest` can start on the first apply. It is written through a write-only argument, so it is never stored in state or plan output. Add the real value out of band (gcloud secrets versions add); Terraform never sees it. | `bool` | `true` | no |
| <a name="input_deletion_protection"></a> [deletion\_protection](#input\_deletion\_protection) | Refuse to destroy the secret while true. Off by default so dev environments tear down cleanly. | `bool` | `false` | no |
| <a name="input_labels"></a> [labels](#input\_labels) | Labels to attach to the secret. | `map(string)` | `{}` | no |
| <a name="input_replica_locations"></a> [replica\_locations](#input\_replica\_locations) | Regions to replicate the secret to. Empty means automatic replication. Secret Manager bills per active version per location, so a single region is both cheaper and keeps the data in one place. | `list(string)` | `[]` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_accessor_member"></a> [accessor\_member](#output\_accessor\_member) | The single principal allowed to read the payload. |
| <a name="output_id"></a> [id](#output\_id) | Full resource ID (projects/<project>/secrets/<secret\_id>). |
| <a name="output_secret_id"></a> [secret\_id](#output\_secret\_id) | Short secret ID, as expected by Cloud Run secret\_key\_ref. |
<!-- END_TF_DOCS -->
