module "artifact_registry" {
  source = "../../modules/artifact_registry"

  project_id    = var.project_id
  location      = var.region
  repository_id = "northstar"

  # Each app's deploy identity may push, and only to this repository.
  writer_members = [
    local.ci.deploy_gateway,
    local.ci.deploy_assistant,
    local.ci.deploy_performance,
  ]
}
