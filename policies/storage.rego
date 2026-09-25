# Cloud Storage guardrails.
#
#   GCS-001  no bucket may be, or be able to become, public
#
# A bucket is public through any of: an IAM grant to allUsers, an ACL entry for allUsers /
# allAuthenticatedUsers, or missing public-access prevention.
package main

import data.lib.tfplan as tf
import rego.v1

bucket_iam_pattern := `^google_storage_bucket_iam_(member|binding)$`

acl_types := {
	"google_storage_bucket_acl",
	"google_storage_default_object_acl",
	"google_storage_object_acl",
	"google_storage_bucket_access_control",
	"google_storage_default_object_access_control",
	"google_storage_object_access_control",
}

# ACL resources name principals in `entity` ("allUsers") or `role_entity` ("READER:allUsers").
acl_entities(a) := entities if {
	from_role_entity := {e |
		some x in a.role_entity
		parts := split(x, ":")
		e := parts[count(parts) - 1]
	}
	from_entity := {e | e := a.entity}
	entities := from_role_entity | from_entity
}

deny contains msg if {
	some rc in tf.changes_matching(bucket_iam_pattern)
	"allUsers" in tf.grant_members(rc)
	msg := sprintf(
		"[GCS-001] %s makes bucket %q publicly readable by granting %s to allUsers.",
		[rc.address, object.get(tf.after(rc), "bucket", "?"), object.get(tf.after(rc), "role", "a role")],
	)
}

deny contains msg if {
	some rc in tf.changes_of_types(acl_types)
	some entity in acl_entities(tf.after(rc))
	entity in tf.public_principals
	msg := sprintf(
		"[GCS-001] %s grants access to %s through an ACL, which makes the bucket or object public.",
		[rc.address, entity],
	)
}

deny contains msg if {
	some rc in tf.changes_of_types({"google_storage_bucket"})
	object.get(tf.after(rc), "public_access_prevention", "inherited") != "enforced"
	msg := sprintf(
		"[GCS-001] bucket %s does not set public_access_prevention = \"enforced\", so a single mistaken grant could make it public.",
		[rc.address],
	)
}

deny contains msg if {
	some rc in tf.changes_of_types({"google_storage_bucket"})
	object.get(tf.after(rc), "uniform_bucket_level_access", false) != true
	msg := sprintf(
		"[GCS-001] bucket %s does not set uniform_bucket_level_access = true. Per-object ACLs are a second, easy-to-miss way to expose data.",
		[rc.address],
	)
}
