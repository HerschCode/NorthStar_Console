module "naming" {
  source = "../../modules/naming"

  project_id     = var.project_id
  project_number = var.project_number
  region         = var.region
}

locals {
  # What the read-only plan identity may see. Every entry is a predefined *viewer* role, and no
  # role here can read a secret's payload or table data.
  plan_roles = [
    "roles/serviceusage.serviceUsageConsumer", # needed because the provider sets user_project_override
    "roles/iam.securityReviewer",              # read IAM policies on the project, service accounts and services
    "roles/run.viewer",
    "roles/secretmanager.viewer", # metadata only; cannot access versions
    "roles/bigquery.metadataViewer",
    "roles/artifactregistry.reader",
    "roles/cloudscheduler.viewer",
    "roles/monitoring.viewer",
    "roles/logging.viewer",
  ]

  # What the apply identity may manage: only the resource types the dev root owns. It is the most
  # powerful identity in the stack, so what it deliberately does NOT have matters more than what it
  # has: no serviceusage.services.enable (cannot switch APIs), no workload identity pool admin
  # (cannot change which repositories may authenticate), no billing role (cannot touch the budget),
  # and roles/resourcemanager.projectIamAdmin only behind a condition (below).
  apply_roles = [
    "roles/serviceusage.serviceUsageConsumer",
    "roles/run.admin",
    "roles/iam.serviceAccountAdmin",
    "roles/artifactregistry.admin",
    "roles/bigquery.admin",
    "roles/cloudscheduler.admin",
    "roles/monitoring.editor",
    "roles/logging.configWriter",
    "roles/secretmanager.admin",
  ]

  ci_service_accounts = {
    deploy_gateway = {
      display_name = "GitHub deploy: llm-security-gateway"
      description  = "Assumed by the gateway repo's deploy workflow. Can push images and roll out revisions of one service; nothing else."
      roles        = []
    }
    deploy_assistant = {
      display_name = "GitHub deploy: operations-assistant"
      description  = "Assumed by the assistant repo's deploy workflow. Can push images and roll out revisions of one service; nothing else."
      roles        = []
    }
    deploy_performance = {
      display_name = "GitHub deploy: operations-performance"
      description  = "Assumed by the performance repo's deploy workflow. Can push images and roll out revisions of one service and one job; nothing else."
      roles        = []
    }
    terraform_plan = {
      display_name = "GitHub Terraform plan (read-only)"
      description  = "Assumed by pull-request plan runs of the infra repo. Read-only."
      roles        = local.plan_roles
    }
    terraform_apply = {
      display_name = "GitHub Terraform apply"
      description  = "Assumed only by the infra repo's manually dispatched apply job, inside a protected GitHub environment."
      roles        = [] # granted by google_project_iam_member.terraform_apply below, where the risk is explained
    }
  }
}

module "sa_ci" {
  source   = "../../modules/service_account"
  for_each = local.ci_service_accounts

  project_id    = var.project_id
  account_id    = module.naming.ci_service_account_ids[each.key]
  display_name  = each.value.display_name
  description   = each.value.description
  project_roles = each.value.roles

  depends_on = [google_project_service.apis]
}

# The apply identity's roles are declared here, next to the reasoning, instead of through the
# service_account module: these are the broadest grants in the repository, so they get their own
# resource and their own explicit, reviewable suppressions. (Trivy's equivalent of the checkov
# skips below is the ignore comment on the next line; the reasoning is identical.)
#trivy:ignore:AVD-GCP-0007
resource "google_project_iam_member" "terraform_apply" {
  # checkov:skip=CKV_GCP_42: Terraform can only create and wire Cloud Run, BigQuery, Artifact Registry, Scheduler, monitoring and secrets with the admin/editor role of each product. Nothing narrower is predefined. Blast radius is bounded: the identity is reachable only from a protected GitHub environment, it holds no role on the budget, workload identity pool or API enablement (those live in this human-only root), and its project IAM edits are limited to one role by the conditional binding below.
  # checkov:skip=CKV_GCP_49: iam.serviceAccountAdmin is needed to create the workload service accounts and set the per-account actAs grants. It is deliberately not iam.serviceAccountKeyAdmin or serviceAccountTokenCreator, and conftest rule KEY-001 forbids creating keys at all.
  for_each = toset(local.apply_roles)

  project = var.project_id
  role    = each.value
  member  = module.naming.ci_members["terraform_apply"]

  depends_on = [module.sa_ci]
}

# The apply identity may edit the project's IAM policy, but only to grant or revoke the one role
# the dev root needs at project scope. Without this condition, projectIamAdmin would let a
# compromised pipeline hand itself roles/owner. (Trivy flags any *Admin role by name; the
# modifiedGrantsByRole condition below is what makes this one safe, and conftest rule IAM-004
# fails the plan if the condition is ever removed.)
#trivy:ignore:AVD-GCP-0007
resource "google_project_iam_member" "terraform_apply_project_iam_admin" {
  project = var.project_id
  role    = "roles/resourcemanager.projectIamAdmin"
  member  = module.naming.ci_members["terraform_apply"]

  condition {
    title       = "only_bigquery_job_user"
    description = "The apply identity can grant or revoke roles/bigquery.jobUser at project level and no other role."
    expression  = "api.getAttribute('iam.googleapis.com/modifiedGrantsByRole', []).hasOnly(['roles/bigquery.jobUser'])"
  }

  depends_on = [module.sa_ci]
}

# Terraform state for the dev root. The plan identity may read it (with -lock=false, so it never
# writes); the apply identity reads and writes it, including the lock object.
resource "google_storage_bucket_iam_member" "dev_state_read" {
  bucket = var.dev_state_bucket
  role   = "roles/storage.objectViewer"
  member = module.naming.ci_members["terraform_plan"]

  depends_on = [module.sa_ci]
}

resource "google_storage_bucket_iam_member" "dev_state_write" {
  bucket = var.dev_state_bucket
  role   = "roles/storage.objectUser"
  member = module.naming.ci_members["terraform_apply"]

  depends_on = [module.sa_ci]
}
