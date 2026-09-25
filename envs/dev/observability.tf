module "monitoring" {
  source = "../../modules/monitoring"

  project_id           = var.project_id
  alert_emails         = var.alert_emails
  gateway_service_name = local.svc.gateway

  # One uptime check per service. The gateway must answer 2xx; the two private services must
  # answer 403, which proves both that they are up and that the IAM check still protects them.
  services = {
    (local.svc.gateway) = {
      host   = local.hosts.gateway
      expect = "public"
    }
    (local.svc.assistant) = {
      host   = local.hosts.assistant
      expect = "private"
    }
    (local.svc.performance) = {
      host   = local.hosts.performance
      expect = "private"
    }
  }

  uptime_period_seconds = var.uptime_period_seconds
  five_xx_threshold     = var.five_xx_threshold
  block_spike_threshold = var.block_spike_threshold
  runbook_url           = var.runbook_url
}
