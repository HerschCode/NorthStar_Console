# wif_github

Workload identity federation for GitHub Actions: a pool, an OIDC provider, and the grants that let
specific workflows become specific service accounts. **No service account key exists anywhere.**

## How a workflow becomes a service account

1. GitHub issues the job a short-lived OIDC token (claims: repository, ref, environment, ...).
2. Google's Security Token Service checks the token against this provider's **attribute
   condition**: the repository owner must match and the repository must be in the explicit allow
   list. Anything else is rejected here, before any IAM binding is consulted.
3. The token becomes a federated principal. It can then impersonate only the service accounts that
   grant `roles/iam.workloadIdentityUser` to a *principal set that matches it*, and each grant is
   pinned to one repository plus **either a git ref or a GitHub environment**.

So the trust is two-layered: which repositories can authenticate at all (provider), and which
authenticated workflow can be which identity (per-service-account grant).

## Design decisions

* **Repository allow-list on `assertion.repository`,** not just the owner, so a new repository under
  the same owner cannot authenticate until it is listed. The conftest rules WIF-001 and WIF-002
  enforce this on every plan.
* **Ref or environment, never a wildcard.** App deploys are pinned to `refs/heads/main`. Terraform
  apply is pinned to a GitHub *environment*: the token only carries that claim for a job that passed
  the environment's approval and branch rules, which is stronger than a branch name.
* **Authoritative grants, resource-level only.** Each service account's
  `roles/iam.workloadIdentityUser` binding is authoritative, so an extra principal added out of band
  is removed on the next apply, and it is never granted on the project.
* **Principals are built from inputs** (`project_number`, `pool_id`), not from the pool's computed
  `name`, so they are known at plan time and can be checked before anything exists.
* **Destroy caveat.** Google soft-deletes pools and providers for 30 days. After a `terraform
  destroy` you cannot re-create the same ID until it is undeleted
  (`gcloud iam workload-identity-pools undelete`) or the ID is changed.

## Usage

```hcl
module "wif_github" {
  source = "../../modules/wif_github"

  project_id          = var.project_id
  project_number      = var.project_number
  github_owner        = "HerschCode"
  github_repositories = ["HerschCode/northstar-infra", "HerschCode/operations-assistant"]

  impersonation = {
    deploy_assistant = {
      service_account_name = module.sa_deploy_assistant.name
      allowed              = [{ repository = "HerschCode/operations-assistant", ref = "refs/heads/main" }]
    }
    terraform_apply = {
      service_account_name = module.sa_tf_apply.name
      allowed              = [{ repository = "HerschCode/northstar-infra", environment = "dev-apply" }]
    }
  }
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
| [google_iam_workload_identity_pool.this](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/iam_workload_identity_pool) | resource |
| [google_iam_workload_identity_pool_provider.github](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/iam_workload_identity_pool_provider) | resource |
| [google_service_account_iam_binding.workload_identity_user](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/service_account_iam_binding) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_github_owner"></a> [github\_owner](#input\_github\_owner) | GitHub user or organisation that owns every repository allowed to federate. Tokens from any other owner are rejected by the provider itself. | `string` | n/a | yes |
| <a name="input_github_repositories"></a> [github\_repositories](#input\_github\_repositories) | Every repository (OWNER/REPO) allowed to exchange a GitHub OIDC token at all. Anything not listed is rejected before any service-account binding is even consulted. | `set(string)` | n/a | yes |
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | Project that hosts the workload identity pool. | `string` | n/a | yes |
| <a name="input_project_number"></a> [project\_number](#input\_project\_number) | Numeric project number (gcloud projects describe <id> --format='value(projectNumber)'). Used to build principal strings that are known at plan time. | `string` | n/a | yes |
| <a name="input_impersonation"></a> [impersonation](#input\_impersonation) | Which GitHub workflows may impersonate which service account. Keyed by an arbitrary label. `service_account_name` is the fully-qualified name (projects/<project>/serviceAccounts/<email>). Each `allowed` entry pins the grant to one repository AND either a git ref (for example refs/heads/main) or a GitHub environment, so a token from another branch, a pull request or another environment cannot use it. Set at most one of `ref` and `environment` per entry. The grant is authoritative for roles/iam.workloadIdentityUser on that service account. | ```map(object({ service_account_name = string allowed = list(object({ repository = string ref = optional(string) environment = optional(string) })) }))``` | `{}` | no |
| <a name="input_pool_id"></a> [pool\_id](#input\_pool\_id) | Workload identity pool ID (4-32 characters of a-z, 0-9, hyphen; the gcp- prefix is reserved). | `string` | `"github"` | no |
| <a name="input_provider_id"></a> [provider\_id](#input\_provider\_id) | Workload identity pool provider ID (4-32 characters of a-z, 0-9, hyphen; the gcp- prefix is reserved). | `string` | `"github-oidc"` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_attribute_condition"></a> [attribute\_condition](#output\_attribute\_condition) | The CEL condition every incoming token must satisfy. |
| <a name="output_pool_name"></a> [pool\_name](#output\_pool\_name) | Pool resource name (projects/<number>/locations/global/workloadIdentityPools/<pool>). Known at plan time. |
| <a name="output_principals"></a> [principals](#output\_principals) | The principalSet members granted per impersonation entry. |
| <a name="output_provider_name"></a> [provider\_name](#output\_provider\_name) | Value for the `workload_identity_provider` input of google-github-actions/auth. Known at plan time. |
<!-- END_TF_DOCS -->
