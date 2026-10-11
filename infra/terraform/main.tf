# Cloud storage for pipeline products on Google Cloud: a bucket that serves COGs and the STAC
# catalogue publicly (read-only), and a least-privilege service account the pipeline writes with.

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  # backend "gcs" { bucket = "<state-bucket>", prefix = "igad-rainfall" }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_storage_bucket" "products" {
  name                        = "${var.project_id}-${var.bucket_suffix}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = var.public_read ? "inherited" : "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  # Daily raw clips are reproducible from CHIRPS, so they expire; published products are kept.
  lifecycle_rule {
    condition {
      age            = var.raw_retention_days
      matches_prefix = ["raw/"]
    }
    action {
      type = "Delete"
    }
  }

  # Browsers (MapLibre, STAC Browser) read COGs with HTTP range requests.
  cors {
    origin          = var.cors_origins
    method          = ["GET", "HEAD"]
    response_header = ["Content-Type", "Content-Range", "Accept-Ranges", "ETag"]
    max_age_seconds = 3600
  }

  labels = var.labels
}

resource "google_storage_bucket_iam_member" "public_read" {
  count  = var.public_read ? 1 : 0
  bucket = google_storage_bucket.products.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}

resource "google_service_account" "pipeline" {
  account_id   = "gha-rainfall-pipeline"
  display_name = "GHA rainfall pipeline writer"
}

resource "google_storage_bucket_iam_member" "pipeline_writer" {
  bucket = google_storage_bucket.products.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.pipeline.email}"
}
