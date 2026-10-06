# monitoring

One uptime check per service, an alert on 5xx responses, and an alert on a spike in the gateway's
blocked requests.

## Design decisions

* **The uptime check for a private service expects HTTP 403.** A private Cloud Run service answers
  an anonymous probe with 403 (the IAM invoker check rejecting it). The check passes *only* on 403,
  so it proves two things at once: the service is up, and it is **still private**. If a private
  service is ever opened to `allUsers` the check flips to failing and pages you, which makes the
  "unauthenticated call returns 403" claim a continuously verified fact rather than a one-off test.
  It also needs no invoker grant for Google's monitoring agent, so the invoker lists stay exactly
  as the architecture states, and the probe never wakes a container (or the database behind it).
* **The public gateway is checked normally** (2xx on `/health`).
* **Alerts are grouped per service** (5xx) so an incident names the service that is failing.
* **The block-rate alert reads structured logs.** The gateway's decision record already has a
  `decision` field (`allow` / `block`); a log-based counter metric counts `block` entries. This
  needs the gateway to write each decision as a JSON line to stdout, which Cloud Run turns into
  `jsonPayload`.
* **Cost.** Uptime checks are free up to one million executions per project per month; three
  services at five-minute intervals from every probe region is roughly 155,000. Alerting policies
  are free now, but the pricing page lists a charge of $0.35 per metric reference per month from
  1 September 2027 (about three references here).

## Usage

```hcl
module "monitoring" {
  source = "../../modules/monitoring"

  project_id           = var.project_id
  alert_emails         = ["you@example.com"]
  gateway_service_name = "llm-security-gateway"

  services = {
    "llm-security-gateway" = { host = "llm-security-gateway-123456789012.us-central1.run.app", expect = "public" }
    "operations-assistant" = { host = "operations-assistant-123456789012.us-central1.run.app", expect = "private" }
  }
}
```

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.11.0 |
| <a name="requirement_google"></a> [google](#requirement\_google) | ~> 8.4 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_google"></a> [google](#provider\_google) | ~> 8.4 |

## Resources

| Name | Type |
| ---- | ---- |
| [google_logging_metric.gateway_blocks](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/logging_metric) | resource |
| [google_monitoring_alert_policy.five_xx](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/monitoring_alert_policy) | resource |
| [google_monitoring_alert_policy.gateway_block_spike](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/monitoring_alert_policy) | resource |
| [google_monitoring_alert_policy.uptime](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/monitoring_alert_policy) | resource |
| [google_monitoring_notification_channel.email](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/monitoring_notification_channel) | resource |
| [google_monitoring_uptime_check_config.this](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/monitoring_uptime_check_config) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_gateway_service_name"></a> [gateway\_service\_name](#input\_gateway\_service\_name) | Cloud Run service whose structured logs are counted for the block-rate alert. | `string` | n/a | yes |
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | Project that owns the monitoring resources. | `string` | n/a | yes |
| <a name="input_services"></a> [services](#input\_services) | One uptime check per Cloud Run service, keyed by service name. `host` is the bare hostname (no scheme). `expect` says what a healthy service looks like from the public internet:   - public:  answers 2xx on `path` (the gateway, the only service anyone may call).   - private: answers 403, because the IAM invoker check rejects an unauthenticated caller.              The check passes only on 403, so it proves the service is up AND still private:              if a private service is ever opened to allUsers the check flips to failing. | ```map(object({ host = string path = optional(string, "/health") expect = string }))``` | n/a | yes |
| <a name="input_alert_emails"></a> [alert\_emails](#input\_alert\_emails) | Email addresses that receive alerts. Empty creates the policies without a notification channel (they still open incidents in the console). | `list(string)` | `[]` | no |
| <a name="input_block_spike_threshold"></a> [block\_spike\_threshold](#input\_block\_spike\_threshold) | Alert when the gateway blocks more than this many requests within five minutes. | `number` | `20` | no |
| <a name="input_five_xx_threshold"></a> [five\_xx\_threshold](#input\_five\_xx\_threshold) | Alert when a service returns more than this many 5xx responses within five minutes. | `number` | `5` | no |
| <a name="input_runbook_url"></a> [runbook\_url](#input\_runbook\_url) | Link included in every alert's documentation. Null omits it. | `string` | `null` | no |
| <a name="input_uptime_period_seconds"></a> [uptime\_period\_seconds](#input\_uptime\_period\_seconds) | How often each uptime check runs. The free allowance is 1 million executions a month; 300 s across all probe regions and three services stays far below it. | `number` | `300` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_alert_policy_ids"></a> [alert\_policy\_ids](#output\_alert\_policy\_ids) | Alert policy IDs keyed by purpose. |
| <a name="output_gateway_block_metric"></a> [gateway\_block\_metric](#output\_gateway\_block\_metric) | Name of the log-based metric that counts blocked gateway requests. |
| <a name="output_notification_channel_ids"></a> [notification\_channel\_ids](#output\_notification\_channel\_ids) | Email notification channel IDs. |
| <a name="output_uptime_check_ids"></a> [uptime\_check\_ids](#output\_uptime\_check\_ids) | Uptime check IDs keyed by service name. |
<!-- END_TF_DOCS -->
