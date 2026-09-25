# Every secret has exactly one reader. Terraform creates the container and a placeholder version;
# the real value is added out of band (see the `secrets` output) and never enters state.
#
# The two database passwords are separate secrets for separate roles on purpose: the API's
# read-only credential cannot be used to write, and the pipeline's write credential is not
# reachable from the internet-facing API.

locals {
  secrets = merge(
    {
      "neon-api-reader-password" = {
        accessor    = "performance"
        env         = "API_DB_PASSWORD"
        description = "Password of the read-only Postgres role used by the operations-performance API."
      }
      "neon-pipeline-writer-password" = {
        accessor    = "pipeline"
        env         = "DB_PASSWORD"
        description = "Password of the read/write Postgres role used by the pipeline job."
      }
    },
    {
      for id, env_name in var.llm_secrets : id => {
        accessor    = "assistant"
        env         = env_name
        description = "LLM provider API key used by operations-assistant."
      }
    },
  )
}

module "secret" {
  source   = "../../modules/secret"
  for_each = local.secrets

  project_id                     = var.project_id
  secret_id                      = each.key
  accessor_service_account_email = module.naming.runtime_service_account_emails[each.value.accessor]
  replica_locations              = [var.region]
  labels                         = { consumer = each.value.accessor }

  # The accessor service account must exist before it can be named in an IAM binding.
  depends_on = [module.sa_runtime]
}
