package main

import rego.v1

provider_with_condition(condition) := change(
	"google_iam_workload_identity_pool_provider.github",
	"google_iam_workload_identity_pool_provider",
	{"oidc": [{"issuer_uri": "https://token.actions.githubusercontent.com"}], "attribute_condition": condition},
)

pool := "projects/123456789012/locations/global/workloadIdentityPools/github"

wif_binding(principal) := change(
	"google_service_account_iam_binding.wif",
	"google_service_account_iam_binding",
	{"service_account_id": "projects/p/serviceAccounts/x@p.iam.gserviceaccount.com", "role": "roles/iam.workloadIdentityUser", "members": [principal]},
)

# ---- WIF-001: the provider must pin repositories ---------------------------------------------

test_wif001_a_provider_without_a_condition_is_denied if {
	fired("WIF-001") with input as plan([provider_with_condition(null)])
}

test_wif001_an_empty_condition_is_denied if {
	fired("WIF-001") with input as plan([provider_with_condition("")])
}

test_wif001_pinning_only_the_owner_is_denied if {
	fired("WIF-001") with input as plan([provider_with_condition("assertion.repository_owner == \"HerschCode\"")])
}

test_wif001_pinning_the_repository_list_passes if {
	not fired("WIF-001") with input as plan([provider_with_condition("assertion.repository_owner == \"HerschCode\" && assertion.repository in [\"HerschCode/northstar-infra\"]")])
}

test_wif001_pinning_one_repository_passes if {
	not fired("WIF-001") with input as plan([provider_with_condition("assertion.repository == \"HerschCode/northstar-infra\"")])
}

test_wif001_other_issuers_are_out_of_scope if {
	not fired("WIF-001") with input as plan([change(
		"google_iam_workload_identity_pool_provider.aws",
		"google_iam_workload_identity_pool_provider",
		{"oidc": [{"issuer_uri": "https://example.com"}], "attribute_condition": null},
	)])
}

# ---- WIF-002: grants must be scoped -------------------------------------------------------------

test_wif002_a_pool_wide_wildcard_is_denied if {
	fired("WIF-002") with input as plan([wif_binding(sprintf("principalSet://iam.googleapis.com/%s/*", [pool]))])
}

test_wif002_a_principal_set_without_an_attribute_is_denied if {
	fired("WIF-002") with input as plan([wif_binding(sprintf("principalSet://iam.googleapis.com/%s/attribute.actor/someone", [pool]))])
}

test_wif002_a_wildcard_attribute_value_is_denied if {
	fired("WIF-002") with input as plan([wif_binding(sprintf("principalSet://iam.googleapis.com/%s/attribute.repository/*", [pool]))])
}

test_wif002_a_repository_scoped_principal_passes if {
	not fired("WIF-002") with input as plan([wif_binding(sprintf("principalSet://iam.googleapis.com/%s/attribute.repository/HerschCode/northstar-infra", [pool]))])
}

test_wif002_a_branch_scoped_principal_passes if {
	not fired("WIF-002") with input as plan([wif_binding(sprintf("principalSet://iam.googleapis.com/%s/attribute.repo_ref/HerschCode/operations-assistant/refs/heads/main", [pool]))])
}

test_wif002_an_environment_scoped_principal_passes if {
	not fired("WIF-002") with input as plan([wif_binding(sprintf("principalSet://iam.googleapis.com/%s/attribute.repo_env/HerschCode/northstar-infra/dev-apply", [pool]))])
}

# ---- KEY-001: no service account keys -----------------------------------------------------------

test_key001_a_service_account_key_is_denied if {
	fired("KEY-001") with input as plan([change(
		"google_service_account_key.k",
		"google_service_account_key",
		{"service_account_id": "projects/p/serviceAccounts/x@p.iam.gserviceaccount.com"},
	)])
}

test_key001_a_service_account_passes if {
	not fired("KEY-001") with input as plan([change("google_service_account.s", "google_service_account", {"account_id": "x"})])
}

# ---- COST-001 / COST-002 -----------------------------------------------------------------------

test_cost001_a_cloud_sql_instance_is_denied if {
	fired("COST-001") with input as plan([change("google_sql_database_instance.db", "google_sql_database_instance", {"name": "db"})])
}

test_cost001_a_load_balancer_rule_is_denied if {
	fired("COST-001") with input as plan([change("google_compute_global_forwarding_rule.lb", "google_compute_global_forwarding_rule", {"name": "lb"})])
}

test_cost001_a_vpc_connector_is_denied if {
	fired("COST-001") with input as plan([change("google_vpc_access_connector.c", "google_vpc_access_connector", {"name": "c"})])
}

test_cost001_cloud_nat_is_denied if {
	fired("COST-001") with input as plan([change("google_compute_router_nat.n", "google_compute_router_nat", {"name": "n"})])
}

test_cost001_a_paid_type_can_be_allowed_deliberately if {
	not fired("COST-001") with input as plan([change("google_sql_database_instance.db", "google_sql_database_instance", {"name": "db"})])
		with data.config.allowed_paid_types as {"google_sql_database_instance"}
}

test_cost001_free_tier_resources_pass if {
	not fired("COST-001") with input as plan([
		change("google_cloud_run_v2_service.s", "google_cloud_run_v2_service", {"name": "s"}),
		change("google_secret_manager_secret.s", "google_secret_manager_secret", {"secret_id": "s"}),
	])
}

test_cost002_warm_instances_warn if {
	warned("COST-002") with input as plan([change(
		"google_cloud_run_v2_service.s",
		"google_cloud_run_v2_service",
		{"name": "s", "template": [{"scaling": [{"min_instance_count": 1}]}]},
	)])
}

test_cost002_scale_to_zero_does_not_warn if {
	not warned("COST-002") with input as plan([change(
		"google_cloud_run_v2_service.s",
		"google_cloud_run_v2_service",
		{"name": "s", "template": [{"scaling": [{"min_instance_count": 0}]}]},
	)])
}

# ---- An empty plan is always fine ------------------------------------------------------------------

test_an_empty_plan_passes if {
	count(deny) == 0 with input as plan([])
}
