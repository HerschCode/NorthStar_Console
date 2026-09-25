package main

import rego.v1

# ---- SEC-001: at most one reader per secret -------------------------------------------------------

test_sec001_two_readers_via_one_binding_are_denied if {
	fired("SEC-001") with input as plan([secret_binding("s", "groq-api-key", [sa("assistant-sa"), sa("gateway-sa")])])
}

test_sec001_two_readers_via_two_member_resources_are_denied if {
	fired("SEC-001") with input as plan([
		change("google_secret_manager_secret_iam_member.a", "google_secret_manager_secret_iam_member", {"secret_id": "groq-api-key", "role": "roles/secretmanager.secretAccessor", "member": sa("assistant-sa")}),
		change("google_secret_manager_secret_iam_member.b", "google_secret_manager_secret_iam_member", {"secret_id": "groq-api-key", "role": "roles/secretmanager.secretAccessor", "member": sa("pipeline-sa")}),
	])
}

test_sec001_a_full_resource_id_and_a_short_id_are_the_same_secret if {
	fired("SEC-001") with input as plan([
		secret_binding("a", "groq-api-key", [sa("assistant-sa")]),
		secret_binding("b", "projects/northstar-test/secrets/groq-api-key", [sa("pipeline-sa")]),
	])
}

test_sec001_one_reader_per_secret_passes if {
	not fired("SEC-001") with input as plan([
		secret_binding("a", "groq-api-key", [sa("assistant-sa")]),
		secret_binding("b", "neon-api-reader-password", [sa("perf-sa")]),
		secret_binding("c", "neon-pipeline-writer-password", [sa("pipeline-sa")]),
	])
}

test_sec001_the_same_principal_twice_is_still_one_reader if {
	not fired("SEC-001") with input as plan([
		secret_binding("a", "groq-api-key", [sa("assistant-sa")]),
		change("google_secret_manager_secret_iam_member.b", "google_secret_manager_secret_iam_member", {"secret_id": "groq-api-key", "role": "roles/secretmanager.secretAccessor", "member": sa("assistant-sa")}),
	])
}

test_sec001_a_viewer_cannot_read_the_payload_so_does_not_count if {
	not fired("SEC-001") with input as plan([
		secret_binding("a", "groq-api-key", [sa("assistant-sa")]),
		change("google_secret_manager_secret_iam_member.v", "google_secret_manager_secret_iam_member", {"secret_id": "groq-api-key", "role": "roles/secretmanager.viewer", "member": sa("gateway-sa")}),
	])
}

test_sec001_a_custom_role_is_assumed_to_read_and_counts if {
	fired("SEC-001") with input as plan([
		secret_binding("a", "groq-api-key", [sa("assistant-sa")]),
		change("google_secret_manager_secret_iam_member.c", "google_secret_manager_secret_iam_member", {"secret_id": "groq-api-key", "role": "projects/northstar-test/roles/customReader", "member": sa("gateway-sa")}),
	])
}

test_sec001_the_secret_admin_role_counts_as_a_reader if {
	fired("SEC-001") with input as plan([
		secret_binding("a", "groq-api-key", [sa("assistant-sa")]),
		change("google_secret_manager_secret_iam_member.adm", "google_secret_manager_secret_iam_member", {"secret_id": "groq-api-key", "role": "roles/secretmanager.admin", "member": sa("gateway-sa")}),
	])
}

# ---- SEC-002: no project-wide secret access ---------------------------------------------------

test_sec002_project_wide_secret_accessor_is_denied if {
	fired("SEC-002") with input as plan([project_member("p", "roles/secretmanager.secretAccessor", sa("assistant-sa"))])
}

test_sec002_project_wide_secret_admin_is_denied_for_anyone_else if {
	fired("SEC-002") with input as plan([project_member("p", "roles/secretmanager.admin", sa("gh-deploy-gateway"))])
}

test_sec002_the_reviewed_exception_for_the_apply_identity_passes if {
	not fired("SEC-002") with input as plan([project_member("p", "roles/secretmanager.admin", sa("gh-tf-apply"))])
}

test_sec002_the_exception_is_role_specific if {
	fired("SEC-002") with input as plan([project_member("p", "roles/secretmanager.secretAccessor", sa("gh-tf-apply"))])
}

test_sec002_a_read_only_metadata_role_is_fine if {
	not fired("SEC-002") with input as plan([project_member("p", "roles/secretmanager.viewer", sa("gh-tf-plan"))])
}

# ---- SEC-003: unknown secret ----------------------------------------------------------------------

test_sec003_a_secret_that_is_not_known_at_plan_time_is_denied if {
	fired("SEC-003") with input as plan([change_unknown(
		"google_secret_manager_secret_iam_binding.s",
		"google_secret_manager_secret_iam_binding",
		{"role": "roles/secretmanager.secretAccessor", "members": [sa("assistant-sa")]},
		{"secret_id": true},
	)])
}

test_sec003_a_configured_secret_id_passes if {
	not fired("SEC-003") with input as plan([secret_binding("s", "groq-api-key", [sa("assistant-sa")])])
}
