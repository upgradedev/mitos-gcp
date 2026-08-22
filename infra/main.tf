# The whole platform, as code.
#
# Everything here was created by hand with gcloud first, which is how it got
# built quickly and is also a rule violation: infrastructure is versioned,
# planned, applied and verified through a pipeline, never through ad-hoc CLI
# state changes. It is also half of what the judging criterion means by
# "reproducible setup".
#
# So this file is the source of truth and the CLI history is not. A teardown and
# a re-apply have to produce a working fleet, and the pipeline proves it rather
# than asserting it.
#
# Deliberately NOT managed here: the project itself and the Terraform state
# bucket, because something has to exist before state can live anywhere. Both
# are created once by `infra/bootstrap.sh`, which is six commands and is not
# expected to be re-run.

terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  backend "gcs" {
    # bucket and prefix come from -backend-config, so the same code can plan
    # against a throwaway environment without editing anything.
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  # One image, three deployments. What differs is the identity Cloud Run starts
  # it with and MITOS_ROLE, and the process can change neither.
  roles = ["reader", "evaluator", "writer"]

  common_env = {
    GOOGLE_CLOUD_PROJECT      = var.project_id
    MITOS_LEDGER              = "firestore"
    MITOS_MODEL               = var.model
    GOOGLE_CLOUD_LOCATION     = "global"
    GOOGLE_GENAI_USE_VERTEXAI = "True"
  }
}

# --------------------------------------------------------------------------
# APIs
# --------------------------------------------------------------------------

resource "google_project_service" "enabled" {
  for_each = toset([
    "run.googleapis.com",
    "firestore.googleapis.com",
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "iam.googleapis.com",
    "secretmanager.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    # Terraform's google provider reads project services through Resource
    # Manager, so managing APIs at all requires this one to be on first. It is
    # not in the list because the product needs it; it is here because the
    # thing that manages the list needs it.
    "cloudresourcemanager.googleapis.com",
  ])

  service = each.value

  # Disabling an API on destroy would take the whole project's other work with
  # it. Teardown means the resources, not the account's ability to have them.
  disable_on_destroy = false
}

# --------------------------------------------------------------------------
# The provenance thread
# --------------------------------------------------------------------------

resource "google_firestore_database" "ledger" {
  name        = "(default)"
  location_id = var.firestore_location
  type        = "FIRESTORE_NATIVE"

  # The ledger is append-only by construction in code; this is the second lock.
  delete_protection_state = "DELETE_PROTECTION_ENABLED"
  deletion_policy         = "DELETE"

  depends_on = [google_project_service.enabled]
}

# --------------------------------------------------------------------------
# Identities. Three for the fleet, one for CI.
# --------------------------------------------------------------------------

resource "google_service_account" "fleet" {
  for_each = toset(local.roles)

  account_id   = "mitos-${each.value}"
  display_name = "Mitos ${each.value}"
  description  = "Cloud Run identity for the Mitos ${each.value} service"

  depends_on = [google_project_service.enabled]
}

resource "google_service_account" "ci" {
  account_id   = "mitos-ci"
  display_name = "Mitos CI (via Workload Identity Federation)"
  description  = "Deliberately poor: it may call Vertex AI and nothing else"

  depends_on = [google_project_service.enabled]
}

# All three append to the thread and all three may call the model. None of this
# is what separates them.
resource "google_project_iam_member" "fleet_ledger" {
  for_each = toset(local.roles)

  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.fleet[each.value].email}"
}

resource "google_project_iam_member" "fleet_model" {
  for_each = toset(local.roles)

  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.fleet[each.value].email}"
}

# The CI identity cannot read Firestore and cannot reach the write credential.
# A test run that could publish would not be a test run.
resource "google_project_iam_member" "ci_model" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.ci.email}"
}

# Pulling the image is a permission, and it is one `gcloud run deploy` grants
# quietly on your behalf. Terraform does not, so an apply against a project that
# was first built by hand fails with a 403 on downloadArtifacts and the message
# names the repository rather than the identity that lacks access.
#
# This is exactly the kind of thing that only surfaces the first time the
# infrastructure is applied from code rather than typed, which is the argument
# for doing that at all.
resource "google_project_iam_member" "fleet_can_pull_the_image" {
  for_each = toset(local.roles)

  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.fleet[each.value].email}"
}

# The Cloud Run service agent is what actually pulls, and it is created by
# Google rather than by us, so it is referenced and never managed.
resource "google_project_iam_member" "run_agent_can_pull_the_image" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:service-${var.project_number}@serverless-robot-prod.iam.gserviceaccount.com"
}

# --------------------------------------------------------------------------
# The one credential that changes anything outside the ledger.
#
# ORG_STANDARDS §9: /{Product}/{Stage}/settings/{Service}/{Key}, flattened with
# "-" the way Secret Manager requires. The stage is a path segment rather than
# baked into the value.
#
# The VALUE is not managed here and must not be. An SSH private key in a
# Terraform plan is an SSH private key in a log.
# --------------------------------------------------------------------------

resource "google_secret_manager_secret" "spec_repo_key" {
  secret_id = "mitos-${var.stage}-settings-writer-spec-repo-deploy-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

# This binding is the entry's central claim, expressed as one resource: exactly
# one identity can read it.
resource "google_secret_manager_secret_iam_member" "only_the_writer" {
  secret_id = google_secret_manager_secret.spec_repo_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.fleet["writer"].email}"
}

# --------------------------------------------------------------------------
# CI authentication, without a long-lived key
# --------------------------------------------------------------------------

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"

  depends_on = [google_project_service.enabled]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"             = "assertion.sub"
    "attribute.repository"       = "assertion.repository"
    "attribute.repository_owner" = "assertion.repository_owner"
  }

  # Without a condition, any repository on GitHub can assume this pool.
  attribute_condition = "assertion.repository_owner=='${var.github_owner}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "ci_from_github" {
  service_account_id = google_service_account.ci.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_owner}/${var.github_repo}"
}

# --------------------------------------------------------------------------
# The fleet
# --------------------------------------------------------------------------

resource "google_cloud_run_v2_service" "fleet" {
  for_each = toset(local.roles)

  name     = "mitos-${each.value}"
  location = var.region

  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.fleet[each.value].email
    timeout         = "900s"

    scaling {
      # The reader holds a Firestore query subscription open. An instance count
      # of zero is a subscription that does not exist.
      min_instance_count = each.value == "reader" ? 1 : 0
      max_instance_count = 4
    }

    containers {
      image = var.image

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
        # Cloud Run throttles CPU to zero between requests by default, which
        # suspends the subscription silently. This is the single setting the
        # control plane depends on.
        cpu_idle = each.value != "reader"
      }

      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name  = "MITOS_ROLE"
        value = each.value
      }

      # Only the writer is told where the specification repository is. The
      # others could not use it if they were.
      dynamic "env" {
        for_each = each.value == "writer" ? [1] : []
        content {
          name  = "MITOS_SPEC_REMOTE"
          value = var.spec_repo_remote
        }
      }
    }
  }

  depends_on = [
    google_project_service.enabled,
    google_firestore_database.ledger,
  ]
}

# A judge opens these with no account, so they are public on purpose.
resource "google_cloud_run_v2_service_iam_member" "public" {
  for_each = toset(local.roles)

  name     = google_cloud_run_v2_service.fleet[each.value].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# The reader orchestrates and cannot write, so it has to ask.
resource "google_cloud_run_v2_service_iam_member" "reader_may_ask_the_writer" {
  name     = google_cloud_run_v2_service.fleet["writer"].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.fleet["reader"].email}"
}
