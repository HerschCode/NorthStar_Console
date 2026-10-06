output "workload_identity_provider" {
  description = "Value of the `workload_identity_provider` input for google-github-actions/auth."
  value       = module.wif_github.provider_name
}

output "ci_service_accounts" {
  description = "Emails of the pipeline identities."
  value       = module.naming.ci_service_account_emails
}

output "github_repository_variables" {
  description = "Actions *variables* (not secrets: none of these is sensitive) to create on each repository. See docs/runbook.md."
  value = {
    all_repositories = {
      GCP_PROJECT_ID     = var.project_id
      GCP_PROJECT_NUMBER = var.project_number
      GCP_REGION         = var.region
      GCP_WIF_PROVIDER   = module.wif_github.provider_name
    }
    (var.github_repos.infra) = {
      GCP_TF_PLAN_SA       = module.naming.ci_service_account_emails["terraform_plan"]
      GCP_TF_APPLY_SA      = module.naming.ci_service_account_emails["terraform_apply"]
      GCP_TF_STATE_BUCKET  = var.dev_state_bucket
      TF_APPLY_ENVIRONMENT = var.terraform_apply_environment
    }
    (var.github_repos.gateway) = {
      GCP_DEPLOY_SA = module.naming.ci_service_account_emails["deploy_gateway"]
    }
    (var.github_repos.assistant) = {
      GCP_DEPLOY_SA = module.naming.ci_service_account_emails["deploy_assistant"]
    }
    (var.github_repos.performance) = {
      GCP_DEPLOY_SA = module.naming.ci_service_account_emails["deploy_performance"]
    }
  }
}

output "budget" {
  description = "The budget that watches this project and its alert levels."
  value = {
    name       = module.budget_guard.budget_name
    thresholds = module.budget_guard.thresholds
  }
}
