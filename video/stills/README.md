# Stills for the demo video

PNG, full browser window, **light or dark, but the same for all of them**.
Save each one under the exact filename below; `video/build.py` looks them up by
name and fails the build if one is missing.

| filename | URL | what must be visible |
|---|---|---|
| `01-cloud-run.png` | https://console.cloud.google.com/run?project=upgradegr-mitos | the three services: mitos-reader, mitos-writer, mitos-evaluator, with region europe-west1 |
| `02-reader-revisions.png` | https://console.cloud.google.com/run/detail/europe-west1/mitos-reader/revisions?project=upgradegr-mitos | the serving revision and its traffic |
| `03-service-accounts.png` | https://console.cloud.google.com/iam-admin/serviceaccounts?project=upgradegr-mitos | the three separate identities |
| `04-model-garden.png` | https://console.cloud.google.com/vertex-ai/model-garden?project=upgradegr-mitos | search `Gemma` first, then capture the Gemma 4 card |
| `05-firestore-ledger.png` | https://console.cloud.google.com/firestore/databases/-default-/data/panel/ledger?project=upgradegr-mitos | the ledger collection with real entries |
| `06-cloud-build.png` | https://console.cloud.google.com/cloud-build/builds?project=upgradegr-mitos | green builds |
| `07-app.png` | https://mitos-reader-437828525303.europe-west1.run.app/ | the product UI, logged out is fine |
| `08-check-run.png` | the Checks tab of a pull request on this repository | the `Mitos change governance` check and its summary |

## Do not capture

- Cloud Run revision **Variables & Secrets** in full: `MITOS_SETUP_TOKEN` is there.
  If you want the two model variables, crop to those two rows only.
- Secret Manager **version values**.
- GitHub App settings: private key, webhook secret, OAuth client secret.
