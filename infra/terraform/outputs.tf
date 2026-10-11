output "bucket" {
  value = google_storage_bucket.products.name
}

output "public_base_url" {
  description = "Set as PUBLIC_BASE_URL so STAC asset hrefs resolve over HTTPS"
  value       = "https://storage.googleapis.com/${google_storage_bucket.products.name}/cog"
}

output "pipeline_service_account" {
  value = google_service_account.pipeline.email
}
