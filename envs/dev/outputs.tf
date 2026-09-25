output "service_urls" {
  description = "Deterministic Cloud Run URLs. Only the gateway answers anonymous callers; the others return 403."
  value       = local.urls
}

output "image_prefix" {
  description = "Prefix for image names: <prefix>/<service>:<tag>."
  value       = module.artifact_registry.image_prefix
}

output "images" {
  description = "Full image names the app CI pipelines push to."
  value = {
    for role, name in local.svc : role => "${module.artifact_registry.image_prefix}/${name}"
  }
}

output "pipeline_job" {
  description = "Cloud Run job that runs the data pipeline."
  value       = module.run_pipeline.name
}

output "secrets" {
  description = "Secrets to fill after the first apply, with the identity that reads each one. Add the real value with `gcloud secrets versions add <id> --data-file=-`."
  value = {
    for id, s in local.secrets : id => {
      readable_by = module.naming.runtime_service_account_emails[s.accessor]
      exposed_as  = s.env
    }
  }
}

output "runtime_service_accounts" {
  description = "Emails of the workload identities."
  value       = module.naming.runtime_service_account_emails
}
