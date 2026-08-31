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
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
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
  # One image, three deployments. What fixes each one's authority is the
  # identity Cloud Run starts it with and MITOS_ROLE, and the process can change
  # neither. Role-scoped environment variables differ as well, set further down
  # this file, so this deliberately no longer reads as an exhaustive list.
  roles = ["reader", "evaluator", "writer"]

  # A Cloud Run URL is deterministic: service, project number, region. It is
  # built rather than referenced because both services come from one for_each,
  # and one instance referencing another inside the same resource is a cycle.
  writer_url = "https://mitos-writer-${var.project_number}.${var.region}.run.app"

  # Deterministic, the same way `writer_url` above is. Deriving the public
  # address from a request header works and is one proxy misconfiguration away
  # from registering a callback URL nobody controls, on an app installation that
  # outlives the deployment that made it.
  reader_url = "https://mitos-reader-${var.project_number}.${var.region}.run.app"

  # MITOS_DEMO_MODE: the public demo IS this deployment. Four endpoints are
  # gated behind it, `/thread`, `/config`, `/run` and `/run/stream`, and it
  # was set nowhere, so all four answered 404 in production while the
  # interface that depends on them was reported as checked. The hero button,
  # the thread view, the boundary view and the repositories view were all
  # dead on the deployed service.
  #
  # The gating itself is right: these are the anonymous demo surfaces and a
  # tenant deployment should not carry them. What was missing is that
  # somebody has to turn them on for the deployment that is the demo.
  #
  # The comment lives here rather than inside the map because a comment
  # between entries splits terraform fmt's alignment group and the build
  # fails on the padding of the line above it.
  common_env = {
    GOOGLE_CLOUD_PROJECT      = var.project_id
    MITOS_PUBLIC_URL          = local.reader_url
    MITOS_DEMO_MODE           = "true"
    MITOS_SETUP_TOKEN         = random_password.setup_token.result
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

# The identity that builds the image, which this file did not describe at all.
#
# Builds ran as `437828525303-compute@developer.gserviceaccount.com`, the default
# compute service account, which holds `roles/editor` on the project. On an entry
# whose argument is that every capability gets its own scoped identity, the step
# that produces the artifact everything else runs held the broadest role in the
# project, and Terraform did not mention it. The infrastructure did not describe
# how its own image is made.
#
# Three roles, each for one thing the build actually does:
#   logWriter          `cloudbuild.yaml` sets `logging: CLOUD_LOGGING_ONLY`
#   artifactregistry   push the image it just built
#   objectViewer       read the source `gcloud builds submit` uploaded
#
# Not `roles/cloudbuild.builds.builder`, which bundles these and more. The
# bundle would work and would be the same shortcut `roles/editor` already is.
# Who may bind a GitHub App to this deployment.
#
# Nobody had to prove anything. `/github/app/new` answered 200 to anyone and the
# manifest callback checked only a state cookie the same response had just set,
# which is CSRF protection doing duty as authorisation. Any visitor could create
# an App under their own GitHub account and have this service store their
# private key, client secret and webhook secret, after which the reader accepts
# deliveries they sign and mints tokens with their key.
#
# A shared secret rather than a session, because before any App exists there are
# no workspaces and no owners, so an owner check has nothing to check against.
# `random_password` rather than a chosen value so it is never typed anywhere,
# and `terraform output -raw setup_token` is how the owner reads it.
resource "random_password" "setup_token" {
  length  = 48
  special = false
}

resource "google_service_account" "build" {
  account_id   = "mitos-build"
  display_name = "Mitos build (via Workload Identity Federation)"
  description  = "May run a build, push the image, and write its logs. Nothing else"

  depends_on = [google_project_service.enabled]
}

resource "google_project_iam_member" "build_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.build.email}"
}

resource "google_project_iam_member" "build_pushes_the_image" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.build.email}"
}

resource "google_project_iam_member" "build_reads_its_own_source" {
  project = var.project_id
  role    = "roles/storage.objectViewer"
  member  = "serviceAccount:${google_service_account.build.email}"
}

# A build submitted with `--service-account` runs AS this identity, and the
# caller has to be allowed to act as it. The caller is this identity, arriving
# from GitHub through Workload Identity Federation, so it acts as itself.
resource "google_service_account_iam_member" "build_acts_as_itself" {
  service_account_id = google_service_account.build.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.build.email}"
}

resource "google_service_account_iam_member" "build_from_github" {
  service_account_id = google_service_account.build.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_owner}/${var.github_repo}"
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

# GitHub's manifest conversion returns credentials exactly once, so the reader
# has to be able to store them. It does NOT follow that it may administer every
# secret in the project.
#
# It did, for one deployment. `roles/secretmanager.admin` at the project level
# includes accessing every version of every secret, and the spec repository
# deploy key is a secret in this project, so the reader could read the one
# credential the entire architecture says it cannot. The live service reported
# `spec_repo_write_credential: {"reachable": true, "detail": "secret accessed"}`
# and the judge suite failed on exactly that line, which is the check earning
# its place.
#
# So the secrets exist before the flow runs, created here with no version, and
# the reader is granted two roles on THOSE secrets. Nothing project-wide.
#
# Three of them, and the names are not decorative. `github_app_manifest_callback`
# writes `{prefix}-private-key`, `{prefix}-client-secret` and
# `{prefix}-webhook-secret`, and three separate places read them back. Removing
# the project-level role took away the runtime's ability to create what it
# writes to, and the first version of this block declared one secret under a
# name nothing reads. Nobody would have noticed until an App was created for
# real, at which point GitHub has already returned the credentials it returns
# exactly once and the callback fails storing them.
#
# `tests/unit/test_secret_names.py` asserts these names against the ones in
# `service/main.py`, so the two cannot drift again in silence.
locals {
  github_app_credentials = ["private-key", "client-secret", "webhook-secret"]
}

resource "google_secret_manager_secret" "github_app" {
  for_each  = toset(local.github_app_credentials)
  secret_id = "mitos-${var.stage}-github-app-${each.value}"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

# Add a version, and read the versions it added. `secretVersionAdder` cannot
# read and `secretAccessor` cannot write, which is why this is two bindings per
# secret rather than one convenient role.
resource "google_secret_manager_secret_iam_member" "reader_writes_github_app" {
  for_each  = google_secret_manager_secret.github_app
  secret_id = each.value.id
  role      = "roles/secretmanager.secretVersionAdder"
  member    = "serviceAccount:${google_service_account.fleet["reader"].email}"
}

resource "google_secret_manager_secret_iam_member" "reader_reads_github_app" {
  for_each  = google_secret_manager_secret.github_app
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.fleet["reader"].email}"
}

# A fourth identity, for the one job that needs Firestore and no model.
#
# The recording used to run on an in-memory ledger, so the first two seconds of
# the submission video read `ledger memory`, and a narration beat claiming the
# recall came out of Firestore was false against its own picture. Recording
# against the real store fixes both.
#
# It gets its own service account rather than borrowing one. `mitos-ci` says of
# itself that it may call Vertex AI and nothing else, and widening it would have
# made that description false in the file a reviewer reads to check the least
# privilege argument. `mitos-tf` can rebuild the project. Neither belongs in a
# workflow whose whole job is to render a video.
#
# What it cannot do is as important as what it can: no `aiplatform.user`, so the
# recording cannot call a model even by accident, which is ADR-009 enforced by
# IAM rather than by an environment variable.
resource "google_service_account" "video" {
  account_id   = "mitos-video"
  display_name = "Mitos video build (via Workload Identity Federation)"
  description  = "Records the demo against the real ledger. No model, no secrets, no deploy."

  depends_on = [google_project_service.enabled]
}

# `datastore.user` is project-wide and includes update and delete. Firestore IAM
# has no per-collection or per-operation scope in its predefined roles, which
# `src/mitos/ledger.py` already says at length rather than implying a narrower
# grant exists. Stated here too, next to the grant, so the two cannot drift.
resource "google_project_iam_member" "video_ledger" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.video.email}"
}

resource "google_service_account_iam_member" "video_from_github" {
  service_account_id = google_service_account.video.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_owner}/${var.github_repo}"
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

# This binding is the entry's central claim, expressed as one resource: of the
# three services in the fleet, exactly one can read it. That is the property
# `/identity` verifies live, by having the other two ask and be refused.
#
# Of the three SERVICES, and not of the project. `mitos-tf@` holds
# `roles/secretmanager.admin` at the project level and `tf@upgrade.net.gr` holds
# `roles/owner`, so both can read this too. That is not a hole to close: an
# identity that can apply Terraform can grant itself anything in one command, and
# `secrets.create` has no secret-level scope, so the grant cannot be narrowed
# without breaking the apply that creates the secret. They are control-plane
# principals, trusted by construction, and the boundary is drawn between the
# workloads. Saying "exactly one identity" without that qualifier was a claim one
# `get-iam-policy` refutes.
resource "google_secret_manager_secret_iam_member" "only_the_writer" {
  secret_id = google_secret_manager_secret.spec_repo_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.fleet["writer"].email}"
}

# The webhook secret. Same reasoning as the deploy key: the container is managed
# and the value is not, because a secret in a plan is a secret in a log.
#
# Only the reader holds the subscription and only the reader receives
# deliveries, so only the reader can read this.
resource "google_secret_manager_secret" "webhook_secret" {
  secret_id = "mitos-${var.stage}-settings-reader-github-webhook-secret"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_iam_member" "only_the_reader" {
  secret_id = google_secret_manager_secret.webhook_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.fleet["reader"].email}"
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

      # The reader orchestrates and cannot write, so it has to know who to ask.
      # Without this it silently produces a plan and publishes nothing, which
      # looks like success. Found by comparing a running service against what
      # this file would recreate, before a teardown rather than after.
      dynamic "env" {
        for_each = each.value == "reader" ? [1] : []
        content {
          name  = "MITOS_WRITER_URL"
          value = local.writer_url
        }
      }

      # Which repositories may wake the fleet. A valid signature proves who sent
      # a delivery, not that we asked for it, so the allowlist is explicit here
      # rather than left to a default in the code.
      dynamic "env" {
        for_each = each.value == "reader" ? [1] : []
        content {
          name  = "MITOS_WEBHOOK_REPOS"
          value = join(",", var.webhook_repositories)
        }
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

# The reader is the public surface, on purpose: a judge opens it with no
# account. The other two are not, and this loop used to grant `allUsers` to all
# three because it iterated `local.roles`.
#
# That was not a theoretical exposure. `POST /execute` on the writer takes a
# path, a body and a branch from the request and publishes them, so anonymous
# invoke on that service was an unauthenticated arbitrary write to the
# specification repository. It was reachable, and confirmed reachable: an empty
# body came back 422 from FastAPI listing the fields it wanted, which is Cloud
# Run having let the caller through.
#
# `for_each` is kept over a one-element set rather than collapsed to a bare
# resource, so the address stays `public["reader"]` and the other two are
# removed from state instead of the reader being destroyed and recreated.
resource "google_cloud_run_v2_service_iam_member" "public" {
  for_each = toset(["reader"])

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
