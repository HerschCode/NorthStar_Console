# Helpers for reading the JSON that `terraform show -json <plan>` produces.
#
# Everything here is about ONE question: what will exist after this plan is applied? Resources
# that are only being deleted cannot introduce a violation, and data sources are not policy
# subjects, so they are ignored.
package lib.tfplan

import rego.v1

# Managed resources that this plan creates or updates.
managed_changes contains rc if {
	some rc in input.resource_changes
	rc.mode == "managed"
	some action in rc.change.actions
	action in {"create", "update"}
}

changes_of_types(types) := {rc |
	some rc in managed_changes
	rc.type in types
}

changes_matching(pattern) := {rc |
	some rc in managed_changes
	regex.match(pattern, rc.type)
}

# Additive members (`*_iam_member`) and authoritative bindings (`*_iam_binding`) of any resource
# type. Whole-policy replacement (`*_iam_policy`) is forbidden outright by IAM-005.
iam_grants := changes_matching(`^google_.+_iam_(member|binding)$`)

# The planned values of a resource. Attributes that are not known until apply are absent here and
# flagged in `after_unknown` instead; see is_unknown.
after(rc) := object.get(rc.change, "after", {})

# True when a top-level attribute is "known after apply" (wholly, or for some list element).
is_unknown(rc, attr) if {
	unknown := object.get(rc.change, "after_unknown", {})
	unknown[attr] == true
}

is_unknown(rc, attr) if {
	unknown := object.get(rc.change, "after_unknown", {})
	some flag in unknown[attr]
	flag == true
}

# Every principal named by an IAM grant: `member` for *_iam_member, `members` for *_iam_binding.
grant_members(rc) := members if {
	a := after(rc)
	single := {m |
		m := a.member
		m != null
	}
	many := {m | some m in a.members}
	members := single | many
}

# The configuration entry (what was WRITTEN in the code) for a resource in resource_changes.
#
# This exists because "unknown after apply" is ambiguous: an argument shows up as unknown both
# when it references something not yet created AND when it is simply left out and the provider
# fills in a default (a Cloud Run service with no service_account gets the default compute
# account, computed at apply). Only the configuration says which of the two happened.
config_resource(rc) := entry if {
	names := [m[1] |
		some m in regex.find_all_string_submatch_n(`module\.([A-Za-z0-9_-]+)`, object.get(rc, "module_address", ""), -1)
	]
	nested := [step |
		some name in names
		some step in ["module_calls", name, "module"]
	]
	path := array.concat(["root_module"], nested)
	some entry in object.get(input.configuration, array.concat(path, ["resources"]), [])
	entry.type == rc.type
	entry.name == rc.name
}

public_principals := {"allUsers", "allAuthenticatedUsers"}

is_public_principal(member) if member in public_principals

# A reviewed, explicit exception (see config.exceptions). Deliberately narrow: it names the rule,
# the role and the principal, so one exception can never silence a rule for anyone else.
excepted(rule_id, role, member) if {
	some e in data.config.exceptions
	e.rule == rule_id
	e.role == role
	startswith(member, e.member_prefix)
}

# Short display form for messages.
show(members) := concat(", ", sort(members))
