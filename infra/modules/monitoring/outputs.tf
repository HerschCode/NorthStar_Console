output "notification_channel_ids" {
  description = "Email notification channel IDs."
  value       = local.channel_ids
}

output "uptime_check_ids" {
  description = "Uptime check IDs keyed by service name."
  value       = { for k, c in google_monitoring_uptime_check_config.this : k => c.uptime_check_id }
}

output "alert_policy_ids" {
  description = "Alert policy IDs keyed by purpose."
  value = {
    uptime              = google_monitoring_alert_policy.uptime.id
    five_xx             = google_monitoring_alert_policy.five_xx.id
    gateway_block_spike = google_monitoring_alert_policy.gateway_block_spike.id
  }
}

output "gateway_block_metric" {
  description = "Name of the log-based metric that counts blocked gateway requests."
  value       = google_logging_metric.gateway_blocks.name
}
