output "pool_name" {
  description = "Pool resource name (projects/<number>/locations/global/workloadIdentityPools/<pool>). Known at plan time."
  value       = local.pool_name

  depends_on = [google_iam_workload_identity_pool.this]
}

output "provider_name" {
  description = "Value for the `workload_identity_provider` input of google-github-actions/auth. Known at plan time."
  value       = local.provider_name

  depends_on = [google_iam_workload_identity_pool_provider.github]
}

output "attribute_condition" {
  description = "The CEL condition every incoming token must satisfy."
  value       = google_iam_workload_identity_pool_provider.github.attribute_condition
}

output "principals" {
  description = "The principalSet members granted per impersonation entry."
  value       = local.members
}
