package main

import rego.v1

good_bucket := change(
	"google_storage_bucket.b",
	"google_storage_bucket",
	{"name": "b", "public_access_prevention": "enforced", "uniform_bucket_level_access": true},
)

# ---- GCS-001: no public buckets ---------------------------------------------------------------

test_gcs001_a_bucket_iam_grant_to_all_users_is_denied if {
	fired("GCS-001") with input as plan([change(
		"google_storage_bucket_iam_member.m",
		"google_storage_bucket_iam_member",
		{"bucket": "b", "role": "roles/storage.objectViewer", "member": "allUsers"},
	)])
}

test_gcs001_a_bucket_iam_binding_containing_all_users_is_denied if {
	fired("GCS-001") with input as plan([change(
		"google_storage_bucket_iam_binding.m",
		"google_storage_bucket_iam_binding",
		{"bucket": "b", "role": "roles/storage.objectViewer", "members": [sa("a"), "allUsers"]},
	)])
}

test_gcs001_an_acl_role_entity_for_all_users_is_denied if {
	fired("GCS-001") with input as plan([change(
		"google_storage_bucket_acl.a",
		"google_storage_bucket_acl",
		{"bucket": "b", "role_entity": ["OWNER:project-owners-1", "READER:allUsers"]},
	)])
}

test_gcs001_an_access_control_entity_for_all_authenticated_users_is_denied if {
	fired("GCS-001") with input as plan([change(
		"google_storage_object_access_control.a",
		"google_storage_object_access_control",
		{"bucket": "b", "object": "o", "role": "READER", "entity": "allAuthenticatedUsers"},
	)])
}

test_gcs001_a_bucket_without_public_access_prevention_is_denied if {
	fired("GCS-001") with input as plan([change(
		"google_storage_bucket.b",
		"google_storage_bucket",
		{"name": "b", "uniform_bucket_level_access": true},
	)])
}

test_gcs001_public_access_prevention_left_to_inherit_is_denied if {
	fired("GCS-001") with input as plan([change(
		"google_storage_bucket.b",
		"google_storage_bucket",
		{"name": "b", "public_access_prevention": "inherited", "uniform_bucket_level_access": true},
	)])
}

test_gcs001_a_bucket_without_uniform_access_is_denied if {
	fired("GCS-001") with input as plan([change(
		"google_storage_bucket.b",
		"google_storage_bucket",
		{"name": "b", "public_access_prevention": "enforced", "uniform_bucket_level_access": false},
	)])
}

test_gcs001_a_locked_down_bucket_passes if {
	not fired("GCS-001") with input as plan([good_bucket])
}

test_gcs001_private_bucket_grants_pass if {
	not fired("GCS-001") with input as plan([change(
		"google_storage_bucket_iam_member.m",
		"google_storage_bucket_iam_member",
		{"bucket": "b", "role": "roles/storage.objectViewer", "member": sa("gh-tf-plan")},
	)])
}

# ---- BQ-001: no public datasets ------------------------------------------------------------------

test_bq001_a_dataset_open_to_all_authenticated_users_is_denied if {
	fired("BQ-001") with input as plan([change(
		"google_bigquery_dataset.d",
		"google_bigquery_dataset",
		{"dataset_id": "d", "access": [{"role": "READER", "special_group": "allAuthenticatedUsers"}]},
	)])
}

test_bq001_a_dataset_open_to_all_users_is_denied if {
	fired("BQ-001") with input as plan([change(
		"google_bigquery_dataset.d",
		"google_bigquery_dataset",
		{"dataset_id": "d", "access": [{"role": "READER", "iam_member": "allUsers"}]},
	)])
}

test_bq001_a_separate_access_resource_is_checked_too if {
	fired("BQ-001") with input as plan([change(
		"google_bigquery_dataset_access.a",
		"google_bigquery_dataset_access",
		{"dataset_id": "d", "role": "READER", "special_group": "allAuthenticatedUsers"},
	)])
}

test_bq001_a_normal_dataset_passes if {
	not fired("BQ-001") with input as plan([change(
		"google_bigquery_dataset.d",
		"google_bigquery_dataset",
		{"dataset_id": "d", "access": [{"role": "READER", "special_group": "projectReaders"}]},
	)])
}

test_bq001_a_dataset_with_no_explicit_access_passes if {
	not fired("BQ-001") with input as plan([change("google_bigquery_dataset.d", "google_bigquery_dataset", {"dataset_id": "d", "access": []})])
}
