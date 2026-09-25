package main

import rego.v1

# ---- IAM-001: primitive roles -----------------------------------------------------------------

test_iam001_editor_for_a_service_account_is_denied if {
	fired("IAM-001") with input as plan([project_member("p", "roles/editor", sa("pipeline-sa"))])
}

test_iam001_owner_is_denied if {
	fired("IAM-001") with input as plan([project_member("p", "roles/owner", sa("pipeline-sa"))])
}

test_iam001_viewer_is_denied if {
	fired("IAM-001") with input as plan([project_member("p", "roles/viewer", sa("pipeline-sa"))])
}

test_iam001_owner_for_a_human_is_denied_too if {
	fired("IAM-001") with input as plan([project_member("p", "roles/owner", "user:someone@example.com")])
}

test_iam001_applies_to_any_resource_type if {
	fired("IAM-001") with input as plan([change(
		"google_service_account_iam_binding.b",
		"google_service_account_iam_binding",
		{"service_account_id": "x", "role": "roles/editor", "members": [sa("a")]},
	)])
}

test_iam001_predefined_roles_pass if {
	not fired("IAM-001") with input as plan([project_member("p", "roles/bigquery.jobUser", sa("perf-sa"))])
}

test_iam001_ignores_resources_that_are_only_being_deleted if {
	not fired("IAM-001") with input as plan([change_deleted(
		"google_project_iam_member.old",
		"google_project_iam_member",
		{"role": "roles/editor", "member": sa("pipeline-sa")},
	)])
}

# ---- IAM-002: impersonation roles at project scope -----------------------------------------

test_iam002_service_account_user_on_the_project_is_denied if {
	fired("IAM-002") with input as plan([project_member("p", "roles/iam.serviceAccountUser", sa("deployer"))])
}

test_iam002_token_creator_on_the_project_is_denied if {
	fired("IAM-002") with input as plan([project_member("p", "roles/iam.serviceAccountTokenCreator", sa("deployer"))])
}

test_iam002_workload_identity_user_on_the_project_is_denied if {
	fired("IAM-002") with input as plan([project_member("p", "roles/iam.workloadIdentityUser", sa("deployer"))])
}

test_iam002_service_account_user_on_one_service_account_passes if {
	not fired("IAM-002") with input as plan([change(
		"google_service_account_iam_binding.b",
		"google_service_account_iam_binding",
		{"service_account_id": "projects/p/serviceAccounts/gateway-sa@p.iam.gserviceaccount.com", "role": "roles/iam.serviceAccountUser", "members": [sa("deployer")]},
	)])
}

# ---- IAM-003: allAuthenticatedUsers ---------------------------------------------------------

test_iam003_all_authenticated_users_is_denied_even_on_the_gateway if {
	fired("IAM-003") with input as plan([run_invoker("gw", "llm-security-gateway", ["allAuthenticatedUsers"])])
}

test_iam003_all_authenticated_users_on_a_dataset_is_denied if {
	fired("IAM-003") with input as plan([change(
		"google_bigquery_dataset_iam_member.m",
		"google_bigquery_dataset_iam_member",
		{"dataset_id": "d", "role": "roles/bigquery.dataViewer", "member": "allAuthenticatedUsers"},
	)])
}

# ---- IAM-004: IAM-admin roles need a limiting condition -----------------------------------

good_condition := [{
	"title": "only_bigquery_job_user",
	"description": "",
	"expression": "api.getAttribute('iam.googleapis.com/modifiedGrantsByRole', []).hasOnly(['roles/bigquery.jobUser'])",
}]

test_iam004_project_iam_admin_without_a_condition_is_denied if {
	fired("IAM-004") with input as plan([project_member("p", "roles/resourcemanager.projectIamAdmin", sa("gh-tf-apply"))])
}

test_iam004_project_iam_admin_with_the_limiting_condition_passes if {
	not fired("IAM-004") with input as plan([change(
		"google_project_iam_member.p",
		"google_project_iam_member",
		{"role": "roles/resourcemanager.projectIamAdmin", "member": sa("gh-tf-apply"), "condition": good_condition},
	)])
}

test_iam004_a_condition_that_does_not_limit_grants_is_denied if {
	fired("IAM-004") with input as plan([change(
		"google_project_iam_member.p",
		"google_project_iam_member",
		{
			"role": "roles/resourcemanager.projectIamAdmin",
			"member": sa("gh-tf-apply"),
			"condition": [{"title": "t", "description": "", "expression": "request.time < timestamp('2030-01-01T00:00:00Z')"}],
		},
	)])
}

test_iam004_security_admin_without_a_condition_is_denied if {
	fired("IAM-004") with input as plan([project_member("p", "roles/iam.securityAdmin", sa("x"))])
}

# ---- IAM-005: whole-policy replacement ------------------------------------------------------

test_iam005_project_iam_policy_is_denied if {
	fired("IAM-005") with input as plan([change("google_project_iam_policy.p", "google_project_iam_policy", {"policy_data": "{}"})])
}

test_iam005_cloud_run_iam_policy_is_denied if {
	fired("IAM-005") with input as plan([change("google_cloud_run_v2_service_iam_policy.p", "google_cloud_run_v2_service_iam_policy", {"name": "x", "policy_data": "{}"})])
}

test_iam005_project_iam_binding_is_denied if {
	fired("IAM-005") with input as plan([change(
		"google_project_iam_binding.b",
		"google_project_iam_binding",
		{"role": "roles/bigquery.jobUser", "members": [sa("a")]},
	)])
}

test_iam005_project_iam_member_passes if {
	not fired("IAM-005") with input as plan([project_member("p", "roles/bigquery.jobUser", sa("perf-sa"))])
}

test_iam005_resource_scoped_binding_passes if {
	not fired("IAM-005") with input as plan([run_invoker("a", "operations-assistant", [sa("gateway-sa")])])
}

# ---- IAM-006: folder / organization / billing account -----------------------------------------

test_iam006_organization_iam_is_denied if {
	fired("IAM-006") with input as plan([change(
		"google_organization_iam_member.o",
		"google_organization_iam_member",
		{"org_id": "1", "role": "roles/bigquery.jobUser", "member": sa("a")},
	)])
}

test_iam006_billing_account_iam_is_denied if {
	fired("IAM-006") with input as plan([change(
		"google_billing_account_iam_member.b",
		"google_billing_account_iam_member",
		{"billing_account_id": "1", "role": "roles/billing.viewer", "member": sa("a")},
	)])
}

# ---- PUB-001: allUsers elsewhere --------------------------------------------------------------

test_pub001_all_users_on_a_dataset_is_denied if {
	fired("PUB-001") with input as plan([change(
		"google_bigquery_dataset_iam_member.m",
		"google_bigquery_dataset_iam_member",
		{"dataset_id": "d", "role": "roles/bigquery.dataViewer", "member": "allUsers"},
	)])
}

test_pub001_all_users_on_a_secret_is_denied if {
	fired("PUB-001") with input as plan([secret_binding("s", "groq-api-key", ["allUsers"])])
}

test_pub001_leaves_cloud_run_to_its_own_rule if {
	not fired("PUB-001") with input as plan([run_invoker("gw", "llm-security-gateway", ["allUsers"])])
}

# ---- UNK-001: values that cannot be checked ---------------------------------------------------

test_unk001_unknown_member_is_denied if {
	fired("UNK-001") with input as plan([change_unknown(
		"google_project_iam_member.p",
		"google_project_iam_member",
		{"role": "roles/bigquery.jobUser"},
		{"member": true},
	)])
}

test_unk001_partially_unknown_members_are_denied if {
	fired("UNK-001") with input as plan([change_unknown(
		"google_cloud_run_v2_service_iam_binding.b",
		"google_cloud_run_v2_service_iam_binding",
		{"name": "x", "role": "roles/run.invoker", "members": [sa("a")]},
		{"members": [false, true]},
	)])
}

test_unk001_known_grants_pass if {
	not fired("UNK-001") with input as plan([project_member("p", "roles/bigquery.jobUser", sa("perf-sa"))])
}
