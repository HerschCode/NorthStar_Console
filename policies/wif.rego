# Workload identity federation guardrails (how GitHub Actions becomes a Google identity).
#
#   WIF-001  a GitHub provider must restrict WHICH repositories may federate
#   WIF-002  a workloadIdentityUser grant must be scoped to a repository, never the whole pool
package main

import data.lib.tfplan as tf
import rego.v1

github_issuer := "https://token.actions.githubusercontent.com"

github_provider(a) if a.oidc[0].issuer_uri == github_issuer

# The condition must test `assertion.repository` itself. Checking only the owner would still let
# every repository the owner has (or later creates) exchange tokens.
pins_repository(a) if {
	c := a.attribute_condition
	c != null
	regex.match(`assertion\.repository\s*(==|in\b)`, c)
}

deny contains msg if {
	some rc in tf.changes_of_types({"google_iam_workload_identity_pool_provider"})
	a := tf.after(rc)
	github_provider(a)
	not pins_repository(a)
	msg := sprintf(
		"[WIF-001] %s accepts GitHub tokens without restricting the repository. Without an attribute_condition on assertion.repository, any repository on GitHub could obtain Google credentials from this pool.",
		[rc.address],
	)
}

# principalSet://iam.googleapis.com/projects/<number>/locations/global/workloadIdentityPools/<pool>/attribute.<name>/<value>
scoped_principal_set_pattern := `^principalSet://iam\.googleapis\.com/projects/[0-9]+/locations/global/workloadIdentityPools/[a-z0-9-]+/attribute\.(repository|repo_ref|repo_env)/[^*]+$`

deny contains msg if {
	some rc in tf.iam_grants
	tf.after(rc).role == "roles/iam.workloadIdentityUser"
	some member in tf.grant_members(rc)
	startswith(member, "principalSet://")
	not regex.match(scoped_principal_set_pattern, member)
	msg := sprintf(
		"[WIF-002] %s lets %s impersonate a service account. It is not scoped to one repository (attribute.repository, attribute.repo_ref or attribute.repo_env), so it could match every identity in the pool.",
		[rc.address, member],
	)
}
