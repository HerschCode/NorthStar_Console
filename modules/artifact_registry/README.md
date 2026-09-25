# artifact_registry

A private Docker repository with a cleanup policy, so old images do not quietly become a bill.

## Design decisions

* **Storage is the cost lever.** The first 0.5 GiB a month is free and each GiB above it is billed,
  and Python images with numpy / onnx / mlflow are large. A `KEEP` policy retains the newest
  `keep_recent_versions` of every image (default 3), untagged versions go after
  `delete_untagged_after_days`, and anything older than `delete_older_than_days` goes unless it is
  one of the newest. Trade-off: Cloud Run cannot start new instances of a revision whose image was
  deleted, so rolling back further than the kept versions needs a rebuild.
* **Scanning is off on purpose.** Artifact Analysis scanning is billed per image. Trivy in each app
  repo's CI scans the image and gates the deploy, so the registry setting is switched off
  explicitly rather than left to be enabled by a later API change.
* **Push access is per repository.** `writer_members` is an authoritative binding of
  `roles/artifactregistry.writer` on this repository only; the deploy identities hold nothing on
  the project.

## Usage

```hcl
module "artifact_registry" {
  source = "../../modules/artifact_registry"

  project_id    = var.project_id
  location      = "us-central1"
  repository_id = "northstar"

  writer_members = ["serviceAccount:gh-deploy-gateway@my-project.iam.gserviceaccount.com"]
}

# Images: us-central1-docker.pkg.dev/<project>/northstar/<service>:<tag>
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
| [google_artifact_registry_repository.this](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/artifact_registry_repository) | resource |
| [google_artifact_registry_repository_iam_binding.writers](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/artifact_registry_repository_iam_binding) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_location"></a> [location](#input\_location) | Repository location. Keep it in the same region as the Cloud Run services to avoid cross-region pulls. | `string` | n/a | yes |
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | Project that owns the repository. | `string` | n/a | yes |
| <a name="input_repository_id"></a> [repository\_id](#input\_repository\_id) | Repository name, e.g. `northstar`. Images live at <location>-docker.pkg.dev/<project>/<repository\_id>/<image>. | `string` | n/a | yes |
| <a name="input_delete_older_than_days"></a> [delete\_older\_than\_days](#input\_delete\_older\_than\_days) | Delete any version older than this many days, except the most recent `keep_recent_versions`. | `number` | `30` | no |
| <a name="input_delete_untagged_after_days"></a> [delete\_untagged\_after\_days](#input\_delete\_untagged\_after\_days) | Delete untagged versions (orphaned layers of re-tagged builds) after this many days. | `number` | `7` | no |
| <a name="input_description"></a> [description](#input\_description) | What lives in this repository. | `string` | `"Container images for the Northstar services"` | no |
| <a name="input_immutable_tags"></a> [immutable\_tags](#input\_immutable\_tags) | Prevent tags from being moved or deleted. Leave off if your CI re-pushes a moving tag such as `latest`. | `bool` | `false` | no |
| <a name="input_keep_recent_versions"></a> [keep\_recent\_versions](#input\_keep\_recent\_versions) | Always keep this many of the most recent versions of each image, regardless of age. Storage is billed above the free 0.5 GB, so this is the main cost lever. Rolling back further than this needs a rebuild, because Cloud Run cannot start instances of a revision whose image is gone. | `number` | `3` | no |
| <a name="input_labels"></a> [labels](#input\_labels) | Labels to attach to the repository. | `map(string)` | `{}` | no |
| <a name="input_writer_members"></a> [writer\_members](#input\_writer\_members) | Principals allowed to push images (roles/artifactregistry.writer on this repository only). Typically the per-app GitHub deploy service accounts. | `set(string)` | `[]` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_image_prefix"></a> [image\_prefix](#output\_image\_prefix) | Prefix to put in front of an image name: <location>-docker.pkg.dev/<project>/<repository>. |
| <a name="output_repository_id"></a> [repository\_id](#output\_repository\_id) | Repository ID. |
<!-- END_TF_DOCS -->
