locals {
  repo = { for k, name in var.github_repos : k => "${var.github_owner}/${name}" }
}

module "wif_github" {
  source = "../../modules/wif_github"

  project_id          = var.project_id
  project_number      = var.project_number
  github_owner        = var.github_owner
  github_repositories = values(local.repo)

  impersonation = {
    # App deploys: one repository, one branch, one service account each.
    deploy_gateway = {
      service_account_name = module.sa_ci["deploy_gateway"].name
      allowed              = [{ repository = local.repo.gateway, ref = var.deploy_ref }]
    }
    deploy_assistant = {
      service_account_name = module.sa_ci["deploy_assistant"].name
      allowed              = [{ repository = local.repo.assistant, ref = var.deploy_ref }]
    }
    deploy_performance = {
      service_account_name = module.sa_ci["deploy_performance"].name
      allowed              = [{ repository = local.repo.performance, ref = var.deploy_ref }]
    }

    # Plan runs on pull requests, so the ref cannot be pinned to main; the identity is read-only.
    terraform_plan = {
      service_account_name = module.sa_ci["terraform_plan"].name
      allowed              = [{ repository = local.repo.infra }]
    }

    # Apply is pinned to a protected GitHub environment rather than a branch: a token is only
    # minted for a job that passed that environment's approval and branch rules.
    terraform_apply = {
      service_account_name = module.sa_ci["terraform_apply"].name
      allowed              = [{ repository = local.repo.infra, environment = var.terraform_apply_environment }]
    }
  }

  depends_on = [google_project_service.apis]
}
