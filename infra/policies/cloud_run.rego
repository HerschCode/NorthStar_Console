# Cloud Run guardrails.
#
#   RUN-001  only the gateway may be invoked by allUsers, and only via roles/run.invoker
#   RUN-002  the IAM invoker check may never be switched off
#   RUN-003  every service and job runs as a dedicated service account
package main

import data.config
import data.lib.tfplan as tf
import rego.v1

# IAM resources that decide who may call a Cloud Run service / job or a function (which is Cloud
# Run underneath).
cloud_run_iam_pattern := `^google_(cloud_run(_v2)?_(service|job)|cloudfunctions2?_function)_iam_(member|binding)$`

# Only services can be public, never jobs or functions.
cloud_run_service_iam_pattern := `^google_cloud_run(_v2)?_service_iam_(member|binding)$`

# The name of the service, job or function an IAM resource is attached to.
target_name(rc) := name if {
	a := tf.after(rc)
	name := object.get(a, "name", object.get(a, "service", object.get(a, "cloud_function", "")))
}

public_grant_allowed(rc) if {
	regex.match(cloud_run_service_iam_pattern, rc.type)
	tf.after(rc).role == "roles/run.invoker"
	target_name(rc) in config.public_cloud_run_services
}

deny contains msg if {
	some rc in tf.changes_matching(cloud_run_iam_pattern)
	"allUsers" in tf.grant_members(rc)
	not public_grant_allowed(rc)
	msg := sprintf(
		"[RUN-001] %s lets anyone on the internet invoke %q. Only %s may be public (and only through roles/run.invoker); every other service, job and function must require a token.",
		[rc.address, target_name(rc), tf.show(config.public_cloud_run_services)],
	)
}

deny contains msg if {
	some rc in tf.changes_of_types({"google_cloud_run_v2_service"})
	tf.after(rc).invoker_iam_disabled == true
	msg := sprintf(
		"[RUN-002] %s sets invoker_iam_disabled = true, which turns the IAM invoker check off and makes the service public with no IAM record of it. Publish the gateway with an explicit allUsers roles/run.invoker binding instead.",
		[rc.address],
	)
}

runtime_resources := {"google_cloud_run_v2_service", "google_cloud_run_v2_job"}

runtime_service_account(rc) := sa if {
	rc.type == "google_cloud_run_v2_service"
	sa := tf.after(rc).template[0].service_account
}

runtime_service_account(rc) := sa if {
	rc.type == "google_cloud_run_v2_job"
	sa := tf.after(rc).template[0].template[0].service_account
}

runtime_service_account_unknown(rc) if {
	rc.type == "google_cloud_run_v2_service"
	rc.change.after_unknown.template[0].service_account == true
}

runtime_service_account_unknown(rc) if {
	rc.type == "google_cloud_run_v2_job"
	rc.change.after_unknown.template[0].template[0].service_account == true
}

has_dedicated_service_account(rc) if {
	sa := runtime_service_account(rc)
	sa != null
	sa != ""
}

# Unknown is only acceptable when the code actually sets a service account (for example from a
# service account created in the same plan). If the argument is missing from the configuration
# the value is unknown because the provider will fill in the default compute account at apply.
has_dedicated_service_account(rc) if {
	runtime_service_account_unknown(rc)
	service_account_configured(rc)
}

service_account_configured(rc) if {
	rc.type == "google_cloud_run_v2_service"
	cfg := tf.config_resource(rc)
	cfg.expressions.template[0].service_account
}

service_account_configured(rc) if {
	rc.type == "google_cloud_run_v2_job"
	cfg := tf.config_resource(rc)
	cfg.expressions.template[0].template[0].service_account
}

deny contains msg if {
	some rc in tf.changes_of_types(runtime_resources)
	not has_dedicated_service_account(rc)
	msg := sprintf(
		"[RUN-003] %s does not set a service account, so it would run as the project's default compute service account (which is usually Editor). Give every service and job its own least-privilege service account.",
		[rc.address],
	)
}

deny contains msg if {
	some rc in tf.changes_of_types(runtime_resources)
	sa := runtime_service_account(rc)
	sa != null
	regex.match(`-compute@developer\.gserviceaccount\.com$`, sa)
	msg := sprintf(
		"[RUN-003] %s runs as the default compute service account (%s). Give every service and job its own least-privilege service account.",
		[rc.address, sa],
	)
}
