# Long-lived credentials.
#
#   KEY-001  no service account keys, ever
#
# Nothing in this stack needs a JSON key: GitHub Actions uses workload identity federation and the
# workloads use their attached service account. A key is a password that never expires.
package main

import data.lib.tfplan as tf
import rego.v1

deny contains msg if {
	some rc in tf.changes_of_types({"google_service_account_key"})
	msg := sprintf(
		"[KEY-001] %s creates a service account key. There must be no long-lived credentials: use workload identity federation for CI and the attached service account for workloads.",
		[rc.address],
	)
}
