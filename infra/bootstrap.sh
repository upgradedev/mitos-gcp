#!/usr/bin/env bash
# Everything Terraform cannot create for itself, which is the project it lives
# in and the bucket its state lives in. Run once, by a human, and not again.
#
# Kept as a script rather than prose so the two things Terraform does not manage
# are still written down and still reviewable.
set -euo pipefail

PROJECT="${1:-upgradegr-mitos}"
BILLING="${2:?usage: bootstrap.sh <project-id> <billing-account-id>}"
BUCKET="${PROJECT}-tfstate"

gcloud projects describe "$PROJECT" >/dev/null 2>&1 \
  || gcloud projects create "$PROJECT" --name="Upgrade Mitos"

gcloud billing projects link "$PROJECT" --billing-account="$BILLING"
# Resource Manager first, and the ordering is not cosmetic: the google
# provider reads project services through it, so Terraform cannot enable the
# API it needs in order to enable APIs.
gcloud services enable cloudresourcemanager.googleapis.com --project "$PROJECT"
gcloud services enable storage.googleapis.com --project "$PROJECT"

gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1 || {
  gcloud storage buckets create "gs://${BUCKET}" \
    --project "$PROJECT" --location=EU --uniform-bucket-level-access
  # State carries resource names and IAM members. Versioning is the difference
  # between a bad apply and a lost afternoon.
  gcloud storage buckets update "gs://${BUCKET}" --versioning
}

# The Terraform identity needs to READ the image, not just deploy it. Cloud Run
# validates the image reference against the caller, so an identity that can
# create services but cannot read the repository fails with a 403 naming the
# repository rather than itself. That is a confusing hour, and it is separate
# from the runtime service accounts, which need the same permission for the
# different reason that they are the ones actually running it.
gcloud projects add-iam-policy-binding "$PROJECT"   --member="serviceAccount:mitos-tf@${PROJECT}.iam.gserviceaccount.com"   --role=roles/artifactregistry.reader --condition=None -q >/dev/null 2>&1 || true

cat <<MSG

Bootstrap complete.

  terraform -chdir=infra init -backend-config="bucket=${BUCKET}" -backend-config="prefix=mitos"

The deploy key is deliberately not managed by Terraform: a private key in a plan
is a private key in a log. Add the value once, by hand, after the first apply:

  gcloud secrets versions add mitos-prod-settings-writer-spec-repo-deploy-key \
    --data-file=- --project ${PROJECT} < path/to/key

MSG
