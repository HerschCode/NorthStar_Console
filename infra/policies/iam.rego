# IAM guardrails. They apply to every `google_*_iam_member` / `google_*_iam_binding` in the plan,
# whatever resource it is attached to.
#
#   IAM-001  primitive roles (owner / editor / viewer) for any principal
#   IAM-002  impersonation-capable roles granted on the whole project
#   IAM-003  allAuthenticatedUsers anywhere
#   IAM-004  IAM-admin roles without a role-grant limiting condition
#   IAM-005  whole-policy replacement, and project-wide authoritative bindings
#   IAM-006  folder / organization / billing-account IAM (out of scope for this repo)
#   PUB-001  allUsers on anything that is not a Cloud Run service, function or bucket
#   UNK-001  grants whose role or members are not known until apply
package main

import data.lib.tfplan as tf
import rego.v1

primitive_roles := {"roles/owner", "roles/editor", "roles/viewer"}

# Roles that mint credentials for, or impersonate, service accounts. On the project they reach
# EVERY service account in it, so they may only ever be granted on one specific service account
# (google_service_account_iam_*).
project_scope_forbidden_roles := {
	"roles/iam.serviceAccountUser",
	"roles/iam.serviceAccountTokenCreator",
	"roles/iam.serviceAccountKeyAdmin",
	"roles/iam.workloadIdentityUser",
}

# Roles that can edit IAM policy. Safe only when a condition restricts WHICH roles they may grant
# (iam.googleapis.com/modifiedGrantsByRole), otherwise they are "Owner with extra steps".
condition_required_roles := {
	"roles/resourcemanager.projectIamAdmin",
	"roles/resourcemanager.folderIamAdmin",
	"roles/resourcemanager.organizationAdmin",
	"roles/iam.securityAdmin",
}

# Resource families that have their own, more specific public-access rule.
public_handled_elsewhere_pattern := `^google_(cloud_run(_v2)?_(service|job)|cloudfunctions2?_function|storage_bucket)_iam_(member|binding)$`

deny contains msg if {
	some rc in tf.iam_grants
	role := tf.after(rc).role
	role in primitive_roles
	msg := sprintf(
		"[IAM-001] %s grants the primitive role %s to %s. Primitive roles are forbidden for every principal: grant a predefined or custom role on the narrowest resource instead.",
		[rc.address, role, tf.show(tf.grant_members(rc))],
	)
}

deny contains msg if {
	some rc in tf.changes_of_types({"google_project_iam_member", "google_project_iam_binding"})
	role := tf.after(rc).role
	role in project_scope_forbidden_roles
	msg := sprintf(
		"[IAM-002] %s grants %s on the WHOLE project to %s. That role allows impersonating every service account in the project; grant it on the one service account that needs it (google_service_account_iam_*).",
		[rc.address, role, tf.show(tf.grant_members(rc))],
	)
}

deny contains msg if {
	some rc in tf.iam_grants
	"allAuthenticatedUsers" in tf.grant_members(rc)
	msg := sprintf(
		"[IAM-003] %s grants %s to allAuthenticatedUsers, which is every Google account on the internet, not just yours. It is never acceptable.",
		[rc.address, object.get(tf.after(rc), "role", "a role")],
	)
}

deny contains msg if {
	some rc in tf.iam_grants
	role := tf.after(rc).role
	role in condition_required_roles
	not limits_granted_roles(rc)
	msg := sprintf(
		"[IAM-004] %s grants %s without a condition. An IAM-admin role must carry api.getAttribute('iam.googleapis.com/modifiedGrantsByRole', []).hasOnly([...]) so it can only hand out the roles it actually needs.",
		[rc.address, role],
	)
}

limits_granted_roles(rc) if {
	some c in tf.after(rc).condition
	contains(c.expression, "iam.googleapis.com/modifiedGrantsByRole")
	contains(c.expression, "hasOnly")
}

deny contains msg if {
	some rc in tf.managed_changes
	regex.match(`^google_.+_iam_policy$`, rc.type)
	msg := sprintf(
		"[IAM-005] %s replaces an entire IAM policy. Whole-policy resources can lock you out and cannot be checked grant by grant; use *_iam_member or a resource-scoped *_iam_binding.",
		[rc.address],
	)
}

deny contains msg if {
	some rc in tf.changes_of_types({"google_project_iam_binding"})
	msg := sprintf(
		"[IAM-005] %s is authoritative for a role across the whole project and silently removes Google-managed service agents from it. Use google_project_iam_member.",
		[rc.address],
	)
}

deny contains msg if {
	some rc in tf.changes_matching(`^google_(folder|organization|billing_account)_iam_.+$`)
	msg := sprintf(
		"[IAM-006] %s manages folder, organization or billing-account IAM. This repository is scoped to one project; change those by hand, outside the pipeline.",
		[rc.address],
	)
}

deny contains msg if {
	some rc in tf.iam_grants
	not regex.match(public_handled_elsewhere_pattern, rc.type)
	"allUsers" in tf.grant_members(rc)
	msg := sprintf(
		"[PUB-001] %s grants %s to allUsers: anyone on the internet, no login required.",
		[rc.address, object.get(tf.after(rc), "role", "a role")],
	)
}

deny contains msg if {
	some rc in tf.iam_grants
	some attr in {"role", "member", "members"}
	tf.is_unknown(rc, attr)
	msg := sprintf(
		"[UNK-001] %s: `%s` is not known until apply, so this grant cannot be checked. Derive it from configuration (for example a service_account module's `member` output) rather than a computed resource attribute.",
		[rc.address, attr],
	)
}
