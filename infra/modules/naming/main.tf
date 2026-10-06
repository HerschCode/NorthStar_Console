# Single source of truth for every name that more than one root module needs to agree on.
#
# The bootstrap root (identities, budget) and the dev root (workloads) are applied separately and
# by different principals, so they cannot read each other's state. Service account emails and
# Cloud Run URLs are pure functions of these names, so both roots derive them from this module
# instead of sharing state or copy-pasting strings.

locals {
  # Cloud Run services, keyed by role in the trilogy. The values are the app repository names.
  services = {
    gateway     = "llm-security-gateway"
    assistant   = "operations-assistant"
    performance = "operations-performance"
  }

  job = "operations-pipeline"

  # Runtime identities: what the workloads run as.
  runtime_service_account_ids = {
    gateway     = "gateway-sa"
    assistant   = "assistant-sa"
    performance = "perf-sa"
    pipeline    = "pipeline-sa"
    scheduler   = "scheduler-sa"
  }

  # Pipeline identities: what GitHub Actions is allowed to become (via workload identity federation).
  ci_service_account_ids = {
    deploy_gateway     = "gh-deploy-gateway"
    deploy_assistant   = "gh-deploy-assistant"
    deploy_performance = "gh-deploy-perf"
    terraform_plan     = "gh-tf-plan"
    terraform_apply    = "gh-tf-apply"
  }

  suffix = "${var.project_id}.iam.gserviceaccount.com"
}
