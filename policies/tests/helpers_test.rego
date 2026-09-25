# Test fixtures. They build resource_changes in the shape `terraform show -json` renders.
package main

import rego.v1

# The Terraform name of a resource is the last dot-separated part of its address, exactly as in
# a real plan ("google_cloud_run_v2_service.gw" -> "gw").
resource_name(address) := parts[count(parts) - 1] if parts := split(address, ".")

# A resource that this plan creates.
change(address, type, after) := {
	"address": address,
	"mode": "managed",
	"type": type,
	"name": resource_name(address),
	"change": {"actions": ["create"], "after": after, "after_unknown": {}},
}

# A resource with attributes that are "known after apply".
change_unknown(address, type, after, unknown) := {
	"address": address,
	"mode": "managed",
	"type": type,
	"name": resource_name(address),
	"change": {"actions": ["create"], "after": after, "after_unknown": unknown},
}

# A resource this plan only deletes (must never cause a violation).
change_deleted(address, type, before) := {
	"address": address,
	"mode": "managed",
	"type": type,
	"name": resource_name(address),
	"change": {"actions": ["delete"], "before": before, "after": null, "after_unknown": {}},
}

plan(changes) := {"resource_changes": changes}

sa(name) := sprintf("serviceAccount:%s@northstar-test.iam.gserviceaccount.com", [name])

# Did a rule with this ID fire (deny) / warn?
fired(id) if {
	some msg in deny
	startswith(msg, sprintf("[%s]", [id]))
}

warned(id) if {
	some msg in warn
	startswith(msg, sprintf("[%s]", [id]))
}

# Convenience builders for the most common resources.
project_member(name, role, member) := change(
	sprintf("google_project_iam_member.%s", [name]),
	"google_project_iam_member",
	{"project": "northstar-test", "role": role, "member": member},
)

run_invoker(name, service, members) := change(
	sprintf("google_cloud_run_v2_service_iam_binding.%s", [name]),
	"google_cloud_run_v2_service_iam_binding",
	{"name": service, "location": "us-central1", "role": "roles/run.invoker", "members": members},
)

secret_binding(name, secret, members) := change(
	sprintf("google_secret_manager_secret_iam_binding.%s", [name]),
	"google_secret_manager_secret_iam_binding",
	{"secret_id": secret, "role": "roles/secretmanager.secretAccessor", "members": members},
)
