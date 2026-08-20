output "service_urls" {
  description = "What a judge opens. Stable across a teardown because the URL carries the project number, not the revision."
  value       = { for r, s in google_cloud_run_v2_service.fleet : r => s.uri }
}

output "identities" {
  description = "The three service accounts the privilege boundary is drawn between."
  value       = { for r, sa in google_service_account.fleet : r => sa.email }
}

output "write_credential_readable_by" {
  description = "The entry's central claim as a single value: exactly one identity."
  value       = google_service_account.fleet["writer"].email
}

output "workload_identity_provider" {
  description = "Paste into the CI workflows. No key is created, so none can leak."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "ci_service_account" {
  value = google_service_account.ci.email
}
