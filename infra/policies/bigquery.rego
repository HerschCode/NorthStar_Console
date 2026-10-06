# BigQuery guardrails.
#
#   BQ-001  no dataset may be readable by the public or by every Google account
package main

import data.lib.tfplan as tf
import rego.v1

dataset_access_entries(rc) := entries if {
	rc.type == "google_bigquery_dataset"
	entries := tf.after(rc).access
}

dataset_access_entries(rc) := [tf.after(rc)] if rc.type == "google_bigquery_dataset_access"

public_access_entry(entry) if entry.special_group == "allAuthenticatedUsers"

public_access_entry(entry) if entry.iam_member in tf.public_principals

deny contains msg if {
	some rc in tf.changes_of_types({"google_bigquery_dataset", "google_bigquery_dataset_access"})
	some entry in dataset_access_entries(rc)
	public_access_entry(entry)
	msg := sprintf(
		"[BQ-001] %s exposes a dataset to the public or to every Google account (%s). Grant access to specific service accounts with google_bigquery_dataset_iam_binding.",
		[rc.address, object.get(entry, "special_group", object.get(entry, "iam_member", "?"))],
	)
}
