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
gcloud services enable storage.googleapis.com --project "$PROJECT"

gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1 || {
  gcloud storage buckets create "gs://${BUCKET}" \
    --project "$PROJECT" --location=EU --uniform-bucket-level-access
  # State carries resource names and IAM members. Versioning is the difference
  # between a bad apply and a lost afternoon.
  gcloud storage buckets update "gs://${BUCKET}" --versioning
}

cat <<MSG

Bootstrap complete.

  terraform -chdir=infra init -backend-config="bucket=${BUCKET}" -backend-config="prefix=mitos"

The deploy key is deliberately not managed by Terraform: a private key in a plan
is a private key in a log. Add the value once, by hand, after the first apply:

  gcloud secrets versions add mitos-prod-settings-writer-spec-repo-deploy-key \
    --data-file=- --project ${PROJECT} < path/to/key

MSG
