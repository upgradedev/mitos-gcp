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
  # try() because Terraform evaluates every output after every single import,
  # and during a partial import this map has one key in it. Indexing a
  # half-populated for_each is an error, and the error names the output rather
  # than the import that was actually running, which is a confusing half hour.
  value = try(google_service_account.fleet["writer"].email, "not yet created")
}

output "workload_identity_provider" {
  description = "Paste into the CI workflows. No key is created, so none can leak."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "ci_service_account" {
  value = google_service_account.ci.email
}
