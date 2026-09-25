# Cost guardrails. The stack is designed to stay inside the free tier, and the realistic way it
# stops doing so is someone adding one always-on resource and forgetting it.
#
#   COST-001  (deny)  resource types that bill continuously (config.paid_resource_types)
#   COST-002  (warn)  Cloud Run services that keep instances warm
package main

import data.config
import data.lib.tfplan as tf
import rego.v1

deny contains msg if {
	some rc in tf.managed_changes
	reason := config.paid_resource_types[rc.type]
	not rc.type in config.allowed_paid_types
	msg := sprintf(
		"[COST-001] %s (%s) is %s. This stack is designed to stay inside the free tier: remove it, or list %q in config.allowed_paid_types together with the reason.",
		[rc.address, rc.type, reason, rc.type],
	)
}

warn contains msg if {
	some rc in tf.changes_of_types({"google_cloud_run_v2_service"})
	min_instances := tf.after(rc).template[0].scaling[0].min_instance_count
	min_instances > 0
	msg := sprintf(
		"[COST-002] %s keeps %d instance(s) warm, which is billed around the clock. Scale to zero unless cold starts are unacceptable.",
		[rc.address, min_instances],
	)
}
