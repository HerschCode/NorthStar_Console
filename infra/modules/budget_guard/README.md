# budget_guard

A monthly billing budget for one project that emails you at chosen **absolute** spend levels, plus
a forecast alert.

## Design decisions

* **Levels are amounts, not percentages.** You say "alert at 100, 400 and 800" and the module
  converts to the fractions the Budget API expects (12.5% / 50% / 100% of an 800 budget). The
  currency must match the billing account's, or the API rejects the budget.
* **It watches gross usage by default.** `credit_types_treatment = EXCLUDE_ALL_CREDITS` means a
  paid resource left running is noticed even while free-trial credits are absorbing the charge.
* **Scoped to one project.** Spend elsewhere on the billing account is ignored.
* **A budget does not stop spending. It only tells you.** Billing data also lags real usage by
  hours, so a runaway resource can cost money before the first email. The real protection is the
  combination of this alert, `max_instances` ceilings, scale-to-zero and the policy that forbids
  always-on resource types (COST-001).
* **Notifications** go to the billing admins by default. `notification_channel_ids` adds Monitoring
  channels; `pubsub_topic_id` streams every update as JSON.

### Not built: an automatic cut-off

An optional Pub/Sub-triggered function that unlinks billing when spend passes the budget is
**deliberately not implemented**. It would need a Cloud Functions gen2 deployment (Cloud Build
service account, Eventarc trigger identity, Pub/Sub publisher grant), none of which can be validated
without a live billing account, and a destructive action should not ship unverified. `pubsub_topic_id`
is the integration point for it. Google's own sample grants the function `roles/billing.admin` on
the whole billing account; per Google's permission reference, unlinking a project needs only
`resourcemanager.projects.deleteBillingAssignment`, i.e. **`roles/billing.projectManager` on the
project**, which is what a real implementation should use.

## Usage

```hcl
module "budget_guard" {
  source = "../../modules/budget_guard"

  billing_account_id = "0123AB-4567CD-89EF01"
  project_number     = "123456789012"
  currency_code      = "INR"
  budget_amount      = 800
  alert_amounts      = [100, 400, 800]
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
| [google_billing_budget.this](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/billing_budget) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_billing_account_id"></a> [billing\_account\_id](#input\_billing\_account\_id) | Billing account that pays for the project, e.g. 0123AB-4567CD-89EF01. | `string` | n/a | yes |
| <a name="input_project_number"></a> [project\_number](#input\_project\_number) | Numeric number of the project the budget is scoped to. Spend from every other project on the billing account is ignored. | `string` | n/a | yes |
| <a name="input_alert_amounts"></a> [alert\_amounts](#input\_alert\_amounts) | Absolute spend levels (in currency\_code) that trigger an email, ascending. They are converted to the percentages of budget\_amount the Budget API expects: with the defaults, 100 / 400 / 800 on an 800 budget become 12.5% / 50% / 100%. | `list(number)` | ```[ 100, 400, 800 ]``` | no |
| <a name="input_budget_amount"></a> [budget\_amount](#input\_budget\_amount) | Monthly budget in whole units of currency\_code. A budget only notifies; it never stops spending. | `number` | `800` | no |
| <a name="input_credit_types_treatment"></a> [credit\_types\_treatment](#input\_credit\_types\_treatment) | How credits count toward spend. EXCLUDE\_ALL\_CREDITS (default) alerts on gross usage, so a paid resource left running is noticed even while free-trial credits are still absorbing the charge. INCLUDE\_ALL\_CREDITS alerts only on what would actually be billed. | `string` | `"EXCLUDE_ALL_CREDITS"` | no |
| <a name="input_currency_code"></a> [currency\_code](#input\_currency\_code) | ISO 4217 currency of the billing account. The API rejects a budget whose currency differs from the account's. | `string` | `"INR"` | no |
| <a name="input_display_name"></a> [display\_name](#input\_display\_name) | Budget name shown in the console (max 60 characters). | `string` | `"northstar-dev monthly budget"` | no |
| <a name="input_forecast_alert_percent"></a> [forecast\_alert\_percent](#input\_forecast\_alert\_percent) | Also alert when *forecast* spend for the month crosses this fraction of the budget (1.0 = 100%). Null disables the forecast alert. | `number` | `1` | no |
| <a name="input_notification_channel_ids"></a> [notification\_channel\_ids](#input\_notification\_channel\_ids) | Cloud Monitoring notification channels (projects/<id>/notificationChannels/<id>) to notify in addition to the billing admins. At most five. | `list(string)` | `[]` | no |
| <a name="input_pubsub_topic_id"></a> [pubsub\_topic\_id](#input\_pubsub\_topic\_id) | Optional Pub/Sub topic (projects/<id>/topics/<name>) that receives every budget update as JSON. This is the integration point for an automated cut-off (a function that unlinks billing); no such function is deployed by this repository yet. Null publishes nothing. | `string` | `null` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_budget_name"></a> [budget\_name](#output\_budget\_name) | Resource name of the budget (billingAccounts/<id>/budgets/<id>). |
| <a name="output_thresholds"></a> [thresholds](#output\_thresholds) | Alert level (in currency\_code) => fraction of the budget the API was given. |
<!-- END_TF_DOCS -->
