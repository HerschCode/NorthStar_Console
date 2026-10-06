module "naming" {
  source = "../../modules/naming"

  project_id     = var.project_id
  project_number = var.project_number
  region         = var.region
}

locals {
  svc     = module.naming.services
  urls    = module.naming.service_urls
  hosts   = module.naming.service_hosts
  runtime = module.naming.runtime_members # serviceAccount:<email> of each workload identity
  ci      = module.naming.ci_members      # serviceAccount:<email> of each pipeline identity

  # One identity per workload, so a compromise of one grants nothing that belongs to another.
  # act_as: who may attach the identity to a revision (roles/iam.serviceAccountUser, on this
  # service account only). The pipeline identities are created by the bootstrap root.
  runtime_service_accounts = {
    gateway = {
      display_name  = "LLM security gateway (runtime)"
      description   = "Runs llm-security-gateway, the only public service. May call operations-assistant; reads no secrets and no data."
      project_roles = []
      act_as        = [local.ci.deploy_gateway, local.ci.terraform_apply]
    }
    assistant = {
      display_name  = "Operations assistant (runtime)"
      description   = "Runs operations-assistant. May call operations-performance and read its own LLM API keys; nothing else."
      project_roles = []
      act_as        = [local.ci.deploy_assistant, local.ci.terraform_apply]
    }
    performance = {
      display_name  = "Operations performance API (runtime)"
      description   = "Runs operations-performance. Reads its read-only database password and queries the analytics dataset (dataViewer plus jobUser)."
      project_roles = ["roles/bigquery.jobUser"] # job creation is a project-scope permission
      act_as        = [local.ci.deploy_performance, local.ci.terraform_apply]
    }
    pipeline = {
      display_name  = "Operations pipeline job (runtime)"
      description   = "Runs the data pipeline job. Reads its read/write database password and edits the two datasets (dataEditor plus jobUser)."
      project_roles = ["roles/bigquery.jobUser"]
      act_as        = [local.ci.deploy_performance, local.ci.terraform_apply]
    }
    scheduler = {
      display_name  = "Pipeline scheduler"
      description   = "Used by Cloud Scheduler to start the pipeline job. Can run that one job and nothing else."
      project_roles = []
      act_as        = [local.ci.terraform_apply]
    }
  }
}

module "sa_runtime" {
  source   = "../../modules/service_account"
  for_each = local.runtime_service_accounts

  project_id                   = var.project_id
  account_id                   = module.naming.runtime_service_account_ids[each.key]
  display_name                 = each.value.display_name
  description                  = each.value.description
  project_roles                = each.value.project_roles
  service_account_user_members = each.value.act_as
}
