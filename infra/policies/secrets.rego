# Secret Manager guardrails.
#
#   SEC-001  a secret must be readable by at most ONE principal
#   SEC-002  no project-wide grant of secret-reading roles (it reaches every secret)
#   SEC-003  secret IAM must name a secret that is known at plan time
package main

import data.lib.tfplan as tf
import rego.v1

secret_iam_types := {
	"google_secret_manager_secret_iam_member",
	"google_secret_manager_secret_iam_binding",
}

# Roles that can NOT read a payload. Everything else, including any custom role we cannot
# inspect, is assumed able to, so an unfamiliar role fails closed.
payload_blind_roles := {
	"roles/secretmanager.viewer",
	"roles/secretmanager.editor",
	"roles/secretmanager.secretVersionAdder",
	"roles/secretmanager.secretVersionManager",
}

can_read_payload(role) if not role in payload_blind_roles

# `projects/p/secrets/x` and `x` name the same secret.
secret_short_id(id) := regex.replace(id, `^projects/[^/]+/secrets/`, "")

# secret => set of principals that can read it, across every grant in the plan.
secret_readers[secret] contains member if {
	some rc in tf.changes_of_types(secret_iam_types)
	a := tf.after(rc)
	can_read_payload(a.role)
	secret := secret_short_id(a.secret_id)
	some member in tf.grant_members(rc)
}

deny contains msg if {
	some secret, readers in secret_readers
	count(readers) > 1
	msg := sprintf(
		"[SEC-001] secret %q is readable by %d principals (%s). A secret must be readable by exactly one service account; if two workloads need the same value, create two secrets.",
		[secret, count(readers), tf.show(readers)],
	)
}

deny contains msg if {
	some rc in tf.changes_matching(`^google_(project|folder|organization)_iam_(member|binding)$`)
	role := tf.after(rc).role
	role in {"roles/secretmanager.secretAccessor", "roles/secretmanager.admin"}
	some member in tf.grant_members(rc)
	not tf.excepted("SEC-002", role, member)
	msg := sprintf(
		"[SEC-002] %s grants %s to %s on the whole project, which lets it read EVERY secret. Grant secretAccessor on the one secret that identity needs (google_secret_manager_secret_iam_*).",
		[rc.address, role, member],
	)
}

deny contains msg if {
	some rc in tf.changes_of_types(secret_iam_types)
	tf.is_unknown(rc, "secret_id")
	msg := sprintf(
		"[SEC-003] %s names a secret that is not known until apply, so who can read it cannot be checked. Reference the secret's configured secret_id, not its computed id.",
		[rc.address],
	)
}
