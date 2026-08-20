#!/usr/bin/env bash
# Destroy the stateless half and let a re-apply bring it back.
#
# This is the part of "teardown, then rebuild from code" that is worth actually
# doing. The three Cloud Run services hold no state: the ledger is in Firestore,
# the credential is in Secret Manager, and the identities outlive the services.
# So destroying them proves the code can recreate a working fleet, and it costs
# a few minutes of downtime rather than an audit trail.
#
# The URLs come back identical. A Cloud Run URL carries the project number, not
# the revision, so nothing a judge has bookmarked breaks.
#
#   ./teardown_services.sh          # destroy
#   terraform apply -var=image=...  # rebuild
#   python ../scripts/judge_uat.py  # prove it
set -euo pipefail

cd "$(dirname "$0")"

echo "Destroying the three services. The ledger, the identities and the"
echo "credential are untouched, which is the point."
terraform destroy -auto-approve \
  -target='google_cloud_run_v2_service.fleet' \
  -target='google_cloud_run_v2_service_iam_member.public' \
  -target='google_cloud_run_v2_service_iam_member.reader_may_ask_the_writer' \
  -var="image=${IMAGE:?set IMAGE to the container image}"

echo
echo "Gone. Now: terraform apply -var=\"image=\$IMAGE\""
