mock_provider "google" {}

variables {
  project_id            = "northstar-test"
  region                = "us-central1"
  name                  = "operations-pipeline"
  image                 = "us-docker.pkg.dev/cloudrun/container/job"
  service_account_email = "pipeline-sa@northstar-test.iam.gserviceaccount.com"
}

run "unscheduled_by_default" {
  command = plan

  assert {
    condition     = length(google_cloud_scheduler_job.trigger) == 0
    error_message = "no scheduler job unless a schedule is given"
  }

  assert {
    condition     = output.scheduler_job_name == null
    error_message = "scheduler_job_name must be null when unscheduled"
  }
}

run "runs_as_the_dedicated_service_account" {
  command = plan

  assert {
    condition     = google_cloud_run_v2_job.this.template[0].template[0].service_account == "pipeline-sa@northstar-test.iam.gserviceaccount.com"
    error_message = "the job must run as the dedicated service account"
  }

  assert {
    condition     = google_cloud_run_v2_job.this.deletion_protection == false
    error_message = "dev environments must tear down cleanly"
  }
}

run "command_and_secret_env_are_rendered" {
  command = plan

  variables {
    command = ["python"]
    args    = ["-m", "scripts.run_pipeline"]
    env     = { DB_HOST = "ep-example.neon.tech" }
    secret_env = {
      DB_PASSWORD = { secret_id = "neon-pipeline-writer-password" }
    }
  }

  assert {
    condition     = google_cloud_run_v2_job.this.template[0].template[0].containers[0].command == tolist(["python"])
    error_message = "command override expected"
  }

  assert {
    condition     = google_cloud_run_v2_job.this.template[0].template[0].containers[0].args == tolist(["-m", "scripts.run_pipeline"])
    error_message = "args override expected"
  }

  assert {
    condition     = length(google_cloud_run_v2_job.this.template[0].template[0].containers[0].env) == 2
    error_message = "one plain and one secret variable expected"
  }
}

run "scheduler_targets_the_run_api_with_an_oauth_token" {
  command = plan

  variables {
    schedule                        = "0 2 * * *"
    scheduler_service_account_email = "scheduler-sa@northstar-test.iam.gserviceaccount.com"
    invoker_members                 = ["serviceAccount:scheduler-sa@northstar-test.iam.gserviceaccount.com"]
  }

  assert {
    condition     = google_cloud_scheduler_job.trigger[0].http_target[0].uri == "https://run.googleapis.com/v2/projects/northstar-test/locations/us-central1/jobs/operations-pipeline:run"
    error_message = "must POST to the jobs:run endpoint of this job"
  }

  assert {
    condition     = google_cloud_scheduler_job.trigger[0].http_target[0].oauth_token[0].service_account_email == "scheduler-sa@northstar-test.iam.gserviceaccount.com"
    error_message = "Google APIs need an OAuth token minted for the scheduler service account"
  }

  assert {
    condition     = length(google_cloud_scheduler_job.trigger[0].http_target[0].oidc_token) == 0
    error_message = "an OIDC token is wrong for run.googleapis.com"
  }

  assert {
    condition     = google_cloud_scheduler_job.trigger[0].paused == true
    error_message = "the schedule must start paused until a real image exists"
  }

  assert {
    condition     = one(google_cloud_run_v2_job_iam_binding.invoker[0].members) == "serviceAccount:scheduler-sa@northstar-test.iam.gserviceaccount.com"
    error_message = "the scheduler's service account must be able to run the job"
  }
}

run "schedule_without_a_service_account_is_blocked" {
  command = plan

  variables {
    schedule = "0 2 * * *"
  }

  expect_failures = [google_cloud_scheduler_job.trigger]
}

run "scheduler_service_account_must_be_an_invoker" {
  command = plan

  variables {
    schedule                        = "0 2 * * *"
    scheduler_service_account_email = "scheduler-sa@northstar-test.iam.gserviceaccount.com"
  }

  expect_failures = [google_cloud_scheduler_job.trigger]
}

run "public_invokers_are_rejected" {
  command = plan

  variables {
    invoker_members = ["allUsers"]
  }

  expect_failures = [var.invoker_members]
}

run "developer_binding_is_job_scoped" {
  command = plan

  variables {
    deployer_members = ["serviceAccount:gh-deploy-performance@northstar-test.iam.gserviceaccount.com"]
  }

  assert {
    condition     = google_cloud_run_v2_job_iam_binding.developer[0].name == "operations-pipeline"
    error_message = "the binding must target this one job"
  }
}
