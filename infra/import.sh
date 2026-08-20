#!/usr/bin/env bash
# Bring the hand-built platform under Terraform, once.
#
# Everything here was created with gcloud before the Terraform existed. A plan
# against an empty state says "33 to add", which would try to recreate what is
# already running. Importing is how the code and the world are reconciled
# without an outage.
#
# Deliberately imports rather than destroys the identities, the secret and
# Firestore:
#
#   - deleting a service account and recreating it with the same id leaves
#     every IAM binding that referenced it as a `deleted:` entry, and the new
#     account has a different unique id. That is a mess, and it proves nothing
#     the plan does not already prove.
#   - the secret holds the deploy key. Terraform does not manage the value and
#     must not, so destroying the container would destroy the key.
#   - Firestore holds the provenance thread. Destroying the audit trail to
#     demonstrate that an audit trail can be recreated is not a good trade.
#
# What IS proven by teardown is the stateless half: `infra/teardown_services.sh`
# destroys the three Cloud Run services and a re-apply brings them back, with
# the same URLs, because a Cloud Run URL carries the project number and not the
# revision.
set -euo pipefail

P="${PROJECT:-upgradegr-mitos}"
PN="${PROJECT_NUMBER:-437828525303}"
REGION="${REGION:-europe-west1}"
TF="terraform -chdir=$(dirname "$0")"

# -F matters. A resource address contains ["..."], and without fixed-string
# matching grep reads the brackets as a character class, so the check never
# matches and every re-run tries to import what it already imported.
have() { $TF state list 2>/dev/null | grep -Fqx "$1"; }
imp() {
  local addr="$1" id="$2"
  if have "$addr"; then
    echo "  already managed: $addr"
  else
    echo "  importing: $addr"
    if ! $TF import -input=false "$addr" "$id" >/dev/null 2>/tmp/imp.err; then
      if grep -q "already managed by Terraform" /tmp/imp.err; then
        echo "    (already in state)"
      else
        cat /tmp/imp.err >&2
        return 1
      fi
    fi
  fi
}

echo "APIs"
for api in run firestore aiplatform artifactregistry cloudbuild iam \
           secretmanager iamcredentials sts; do
  imp "google_project_service.enabled[\"${api}.googleapis.com\"]" "$P/${api}.googleapis.com"
done

echo "Firestore"
imp "google_firestore_database.ledger" "projects/$P/databases/(default)"

echo "Identities"
for role in reader evaluator writer; do
  imp "google_service_account.fleet[\"$role\"]" \
      "projects/$P/serviceAccounts/mitos-$role@$P.iam.gserviceaccount.com"
done
imp "google_service_account.ci" \
    "projects/$P/serviceAccounts/mitos-ci@$P.iam.gserviceaccount.com"

echo "Project IAM"
for role in reader evaluator writer; do
  imp "google_project_iam_member.fleet_ledger[\"$role\"]" \
      "$P roles/datastore.user serviceAccount:mitos-$role@$P.iam.gserviceaccount.com"
  imp "google_project_iam_member.fleet_model[\"$role\"]" \
      "$P roles/aiplatform.user serviceAccount:mitos-$role@$P.iam.gserviceaccount.com"
done
imp "google_project_iam_member.ci_model" \
    "$P roles/aiplatform.user serviceAccount:mitos-ci@$P.iam.gserviceaccount.com"

echo "The write credential"
SECRET="mitos-prod-settings-writer-spec-repo-deploy-key"
imp "google_secret_manager_secret.spec_repo_key" "projects/$P/secrets/$SECRET"
imp "google_secret_manager_secret_iam_member.only_the_writer" \
    "projects/$P/secrets/$SECRET roles/secretmanager.secretAccessor serviceAccount:mitos-writer@$P.iam.gserviceaccount.com"

echo "Workload Identity"
imp "google_iam_workload_identity_pool.github" \
    "projects/$P/locations/global/workloadIdentityPools/github"
imp "google_iam_workload_identity_pool_provider.github" \
    "projects/$P/locations/global/workloadIdentityPools/github/providers/github-oidc"
imp "google_service_account_iam_member.ci_from_github" \
    "projects/$P/serviceAccounts/mitos-ci@$P.iam.gserviceaccount.com roles/iam.workloadIdentityUser principalSet://iam.googleapis.com/projects/$PN/locations/global/workloadIdentityPools/github/attribute.repository/upgradedev/mitos-gcp"

echo "Cloud Run"
for role in reader evaluator writer; do
  imp "google_cloud_run_v2_service.fleet[\"$role\"]" \
      "projects/$P/locations/$REGION/services/mitos-$role"
  imp "google_cloud_run_v2_service_iam_member.public[\"$role\"]" \
      "projects/$P/locations/$REGION/services/mitos-$role roles/run.invoker allUsers"
done
imp "google_cloud_run_v2_service_iam_member.reader_may_ask_the_writer" \
    "projects/$P/locations/$REGION/services/mitos-writer roles/run.invoker serviceAccount:mitos-reader@$P.iam.gserviceaccount.com"

echo
echo "Imported. A plan should now show what genuinely differs between the code"
echo "and the world, which is the only interesting output."
