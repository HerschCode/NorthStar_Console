package main

import rego.v1

service(name, sa_email) := change(
	sprintf("google_cloud_run_v2_service.%s", [name]),
	"google_cloud_run_v2_service",
	{
		"name": name,
		"invoker_iam_disabled": false,
		"template": [{"service_account": sa_email, "scaling": [{"min_instance_count": 0, "max_instance_count": 2}]}],
	},
)

job(name, sa_email) := change(
	sprintf("google_cloud_run_v2_job.%s", [name]),
	"google_cloud_run_v2_job",
	{"name": name, "template": [{"template": [{"service_account": sa_email}]}]},
)

# ---- RUN-001: only the gateway is public -----------------------------------------------------

test_run001_a_private_service_made_public_is_denied if {
	fired("RUN-001") with input as plan([run_invoker("a", "operations-assistant", ["allUsers"])])
}

test_run001_the_performance_api_made_public_is_denied if {
	fired("RUN-001") with input as plan([run_invoker("p", "operations-performance", [sa("assistant-sa"), "allUsers"])])
}

test_run001_the_gateway_may_be_public_through_invoker if {
	not fired("RUN-001") with input as plan([run_invoker("gw", "llm-security-gateway", ["allUsers"])])
}

test_run001_the_gateway_may_not_hand_allusers_any_other_role if {
	fired("RUN-001") with input as plan([change(
		"google_cloud_run_v2_service_iam_binding.gw",
		"google_cloud_run_v2_service_iam_binding",
		{"name": "llm-security-gateway", "role": "roles/run.admin", "members": ["allUsers"]},
	)])
}

test_run001_private_invokers_pass if {
	not fired("RUN-001") with input as plan([run_invoker("a", "operations-assistant", [sa("gateway-sa")])])
}

test_run001_additive_member_resources_are_checked_too if {
	fired("RUN-001") with input as plan([change(
		"google_cloud_run_v2_service_iam_member.m",
		"google_cloud_run_v2_service_iam_member",
		{"name": "operations-assistant", "role": "roles/run.invoker", "member": "allUsers"},
	)])
}

test_run001_legacy_v1_service_resource_is_checked_too if {
	fired("RUN-001") with input as plan([change(
		"google_cloud_run_service_iam_member.m",
		"google_cloud_run_service_iam_member",
		{"service": "operations-assistant", "role": "roles/run.invoker", "member": "allUsers"},
	)])
}

test_run001_legacy_v1_gateway_may_be_public if {
	not fired("RUN-001") with input as plan([change(
		"google_cloud_run_service_iam_member.m",
		"google_cloud_run_service_iam_member",
		{"service": "llm-security-gateway", "role": "roles/run.invoker", "member": "allUsers"},
	)])
}

test_run001_a_job_can_never_be_public_even_with_the_gateways_name if {
	fired("RUN-001") with input as plan([change(
		"google_cloud_run_v2_job_iam_member.m",
		"google_cloud_run_v2_job_iam_member",
		{"name": "llm-security-gateway", "role": "roles/run.invoker", "member": "allUsers"},
	)])
}

test_run001_a_function_can_never_be_public if {
	fired("RUN-001") with input as plan([change(
		"google_cloudfunctions2_function_iam_member.m",
		"google_cloudfunctions2_function_iam_member",
		{"cloud_function": "budget-killswitch", "role": "roles/cloudfunctions.invoker", "member": "allUsers"},
	)])
}

test_run001_the_allow_list_is_configurable if {
	not fired("RUN-001") with input as plan([run_invoker("a", "operations-assistant", ["allUsers"])])
		with data.config.public_cloud_run_services as {"operations-assistant"}
}

# ---- RUN-002: the invoker check cannot be disabled ------------------------------------------

test_run002_invoker_iam_disabled_is_denied if {
	fired("RUN-002") with input as plan([change(
		"google_cloud_run_v2_service.gw",
		"google_cloud_run_v2_service",
		{"name": "llm-security-gateway", "invoker_iam_disabled": true, "template": [{"service_account": sa("gateway-sa")}]},
	)])
}

test_run002_normal_services_pass if {
	not fired("RUN-002") with input as plan([service("operations-assistant", "assistant-sa@northstar-test.iam.gserviceaccount.com")])
}

# ---- RUN-003: dedicated service accounts ------------------------------------------------------

test_run003_a_service_without_a_service_account_is_denied if {
	fired("RUN-003") with input as plan([change(
		"google_cloud_run_v2_service.s",
		"google_cloud_run_v2_service",
		{"name": "s", "template": [{"service_account": null}]},
	)])
}

test_run003_the_default_compute_account_is_denied if {
	fired("RUN-003") with input as plan([service("s", "123456789012-compute@developer.gserviceaccount.com")])
}

test_run003_a_job_without_a_service_account_is_denied if {
	fired("RUN-003") with input as plan([change(
		"google_cloud_run_v2_job.j",
		"google_cloud_run_v2_job",
		{"name": "j", "template": [{"template": [{"service_account": ""}]}]},
	)])
}

test_run003_a_job_on_the_default_compute_account_is_denied if {
	fired("RUN-003") with input as plan([job("j", "123456789012-compute@developer.gserviceaccount.com")])
}

test_run003_a_dedicated_account_passes if {
	not fired("RUN-003") with input as plan([
		service("s", "assistant-sa@northstar-test.iam.gserviceaccount.com"),
		job("j", "pipeline-sa@northstar-test.iam.gserviceaccount.com"),
	])
}

# "Unknown" is ambiguous, so the rule consults the configuration (what was written in the code).
# Root-level service, service_account set from something not yet created: fine.
test_run003_an_account_created_in_the_same_plan_passes if {
	p := object.union(
		plan([change_unknown(
			"google_cloud_run_v2_service.s",
			"google_cloud_run_v2_service",
			{"name": "s", "template": [{}]},
			{"template": [{"service_account": true}]},
		)]),
		{"configuration": {"root_module": {"resources": [{
			"address": "google_cloud_run_v2_service.s",
			"type": "google_cloud_run_v2_service",
			"name": "s",
			"expressions": {"template": [{"service_account": {"references": ["google_service_account.s.email"]}}]},
		}]}}},
	)
	not fired("RUN-003") with input as p
}

# Same shape, but the code never sets service_account: the provider fills in the default compute
# account at apply, which is why the plan shows it as unknown. Must be denied.
test_run003_an_omitted_service_account_is_denied_even_though_the_plan_shows_it_as_unknown if {
	p := object.union(
		plan([change_unknown(
			"google_cloud_run_v2_service.s",
			"google_cloud_run_v2_service",
			{"name": "s", "template": [{}]},
			{"template": [{"service_account": true}]},
		)]),
		{"configuration": {"root_module": {"resources": [{
			"address": "google_cloud_run_v2_service.s",
			"type": "google_cloud_run_v2_service",
			"name": "s",
			"expressions": {"template": [{"containers": [{"image": {"constant_value": "x"}}]}]},
		}]}}},
	)
	fired("RUN-003") with input as p
}

# A service inside a module (the normal case here): the configuration lives under module_calls.
test_run003_a_module_service_whose_account_comes_from_a_variable_passes if {
	rc := object.union(
		change_unknown(
			"module.run_gateway.google_cloud_run_v2_service.this",
			"google_cloud_run_v2_service",
			{"name": "gw", "template": [{}]},
			{"template": [{"service_account": true}]},
		),
		{"module_address": "module.run_gateway"},
	)
	p := object.union(plan([rc]), {"configuration": {"root_module": {"module_calls": {"run_gateway": {"module": {"resources": [{
		"address": "google_cloud_run_v2_service.this",
		"type": "google_cloud_run_v2_service",
		"name": "this",
		"expressions": {"template": [{"service_account": {"references": ["var.service_account_email"]}}]},
	}]}}}}}})
	not fired("RUN-003") with input as p
}

test_run003_a_job_that_omits_the_account_is_denied if {
	p := object.union(
		plan([change_unknown(
			"google_cloud_run_v2_job.j",
			"google_cloud_run_v2_job",
			{"name": "j", "template": [{"template": [{}]}]},
			{"template": [{"template": [{"service_account": true}]}]},
		)]),
		{"configuration": {"root_module": {"resources": [{
			"address": "google_cloud_run_v2_job.j",
			"type": "google_cloud_run_v2_job",
			"name": "j",
			"expressions": {"template": [{"template": [{"containers": [{"image": {"constant_value": "x"}}]}]}]},
		}]}}},
	)
	fired("RUN-003") with input as p
}
