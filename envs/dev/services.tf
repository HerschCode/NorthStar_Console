locals {
  probe_path = var.use_http_startup_probes ? "/health" : null

  # Plain (non-secret) database settings shared by the API and the pipeline job.
  neon_common_env = {
    DB_HOST    = var.neon_host
    DB_PORT    = "5432"
    DB_NAME    = var.neon_database
    DB_SSLMODE = "require" # Neon rejects unencrypted connections
  }

  bq_env = {
    GCP_PROJECT_ID       = var.project_id
    BQ_DATASET_STAGING   = local.bq_staging_dataset
    BQ_DATASET_ANALYTICS = local.bq_analytics_dataset
  }
}

# ---- P3: llm-security-gateway -- the only public service ---------------------------------------
module "run_gateway" {
  source = "../../modules/cloud_run_service"

  project_id            = var.project_id
  region                = var.region
  name                  = local.svc.gateway
  description           = "Public front door. Screens prompts and responses, then forwards to operations-assistant with a Google ID token."
  image                 = var.placeholder_image
  service_account_email = module.sa_runtime["gateway"].email
  memory                = var.gateway_memory
  max_instances         = var.max_instances
  startup_probe_path    = local.probe_path
  labels                = { app = "gateway", exposure = "public" }

  env = {
    GATEWAY_LITE            = "0"
    EMBEDDING_BACKEND       = "tfidf"
    AUTH_MODE               = "google_id_token"    # outbound auth: an ID token, not a static key (default is api_key)
    OPS_ASSISTANT_URL       = local.urls.assistant # also the audience of the ID token it must mint
    OPS_ASSISTANT_CHAT_PATH = "/chat"              # the authenticated route, not the public keyless /demo/chat
    OPS_ASSISTANT_TIMEOUT   = "120"
  }

  # The single public entry point of the whole system, and the only place these two keys are turned.
  invoker_members = ["allUsers"]
  allow_public    = true

  deployer_members = [local.ci.deploy_gateway]

  depends_on = [module.sa_runtime]
}

# ---- P2: operations-assistant -- private, callable only by the gateway ---------------------------
module "run_assistant" {
  source = "../../modules/cloud_run_service"

  project_id            = var.project_id
  region                = var.region
  name                  = local.svc.assistant
  description           = "LLM agent with retrieval and tools. Private: only the gateway's service account may invoke it."
  image                 = var.placeholder_image
  service_account_email = module.sa_runtime["assistant"].email
  memory                = var.assistant_memory
  max_instances         = var.max_instances
  startup_probe_path    = local.probe_path
  labels                = { app = "assistant", exposure = "private" }

  env = {
    AGENT_PROVIDER          = var.agent_provider
    AGENT_MODEL             = var.agent_model
    AUTH_MODE               = "google_id_token"      # outbound auth: an ID token, not a static key (default is api_key)
    OPS_PERFORMANCE_API_URL = local.urls.performance # also the audience of the ID token it must mint
    CHROMA_PERSIST_DIR      = "./data/chroma"
  }

  secret_env = {
    for id, env_name in var.llm_secrets : env_name => { secret_id = module.secret[id].secret_id }
  }

  invoker_members  = [local.runtime.gateway]
  deployer_members = [local.ci.deploy_assistant]

  depends_on = [module.sa_runtime, module.secret]
}

# ---- P1: operations-performance -- private, callable only by the assistant -------------------------
module "run_performance" {
  source = "../../modules/cloud_run_service"

  project_id            = var.project_id
  region                = var.region
  name                  = local.svc.performance
  description           = "Analytics API over Postgres and BigQuery. Private: only the assistant's service account may invoke it."
  image                 = var.placeholder_image
  service_account_email = module.sa_runtime["performance"].email
  memory                = var.performance_memory
  max_instances         = var.max_instances
  startup_probe_path    = local.probe_path
  labels                = { app = "performance", exposure = "private" }

  env = merge(local.neon_common_env, local.bq_env, {
    API_DB_USER = var.neon_api_user
  })

  secret_env = {
    API_DB_PASSWORD = { secret_id = module.secret["neon-api-reader-password"].secret_id }
  }

  invoker_members  = [local.runtime.assistant]
  deployer_members = [local.ci.deploy_performance]

  depends_on = [module.sa_runtime, module.secret, module.bq_analytics]
}

# ---- Data pipeline: Cloud Scheduler -> Cloud Run job ------------------------------------------------
module "run_pipeline" {
  source = "../../modules/cloud_run_job"

  project_id            = var.project_id
  region                = var.region
  name                  = module.naming.job
  image                 = var.placeholder_job_image
  service_account_email = module.sa_runtime["pipeline"].email
  memory                = var.pipeline_memory
  command               = var.pipeline_command
  args                  = var.pipeline_args
  labels                = { app = "pipeline", exposure = "none" }

  env = merge(local.neon_common_env, local.bq_env, {
    DB_USER = var.neon_pipeline_user
  })

  secret_env = {
    DB_PASSWORD = { secret_id = module.secret["neon-pipeline-writer-password"].secret_id }
  }

  # Cloud Scheduler is the only thing allowed to start it.
  invoker_members                 = [local.runtime.scheduler]
  scheduler_service_account_email = module.naming.runtime_service_account_emails["scheduler"]
  schedule                        = var.pipeline_schedule
  schedule_paused                 = var.pipeline_schedule_paused

  deployer_members = [local.ci.deploy_performance]

  depends_on = [module.sa_runtime, module.secret, module.bq_staging, module.bq_analytics]
}
