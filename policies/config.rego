# Reviewable configuration for the policies. Changing anything in this file weakens or shifts a
# guardrail, so it is covered by CODEOWNERS and every entry carries its justification.
package config

import rego.v1

# The ONLY Cloud Run services that may be invoked by allUsers, and only with roles/run.invoker.
# This is the internet-facing front door; everything behind it must require a token.
public_cloud_run_services := {"llm-security-gateway"}

# Deliberate, narrow exceptions. Each names the rule, the role and the principal it applies to.
exceptions := [{
	"rule": "SEC-002",
	"member_prefix": "serviceAccount:gh-tf-apply@",
	"role": "roles/secretmanager.admin",
	"reason": concat(" ", [
		"The Terraform apply identity must create secrets and manage their IAM bindings, which",
		"secretmanager.admin provides (it also allows reading payloads: whoever can set a",
		"secret's IAM policy can grant themselves access, so no narrower role would change that).",
		"Compensating controls: the identity is only reachable from a protected GitHub",
		"environment, and Secret Manager DATA_READ audit logs are enabled in envs/bootstrap.",
	]),
}]

# Resource types that cost money continuously, whether or not they are used. The stack is meant
# to stay inside the free tier (see docs/cost.md), so adding one needs a conscious decision:
# either remove it, or list its type in allowed_paid_types together with the reason.
paid_resource_types := {
	"google_sql_database_instance": "Cloud SQL, billed per hour",
	"google_compute_instance": "a VM, billed per hour",
	"google_compute_forwarding_rule": "a load balancer forwarding rule, billed per hour",
	"google_compute_global_forwarding_rule": "a load balancer forwarding rule, billed per hour",
	"google_compute_router_nat": "Cloud NAT, billed per hour plus data",
	"google_vpc_access_connector": "a Serverless VPC Access connector, billed for its always-on instances",
	"google_compute_security_policy": "Cloud Armor, billed per policy and per request",
	"google_compute_address": "a reserved IP address, billed while reserved",
	"google_compute_global_address": "a reserved IP address, billed while reserved",
	"google_container_cluster": "GKE, billed per cluster and node",
	"google_redis_instance": "Memorystore, billed per hour",
}

allowed_paid_types := set()
