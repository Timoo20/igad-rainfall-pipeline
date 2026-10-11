variable "project_id" {
  description = "Google Cloud project ID"
  type        = string
}

variable "region" {
  description = "Bucket location; africa-south1 keeps data in the region"
  type        = string
  default     = "africa-south1"
}

variable "bucket_suffix" {
  type    = string
  default = "gha-rainfall"
}

variable "public_read" {
  description = "Serve products publicly (open data). Set false for restricted datasets."
  type        = bool
  default     = true
}

variable "raw_retention_days" {
  type    = number
  default = 60
}

variable "cors_origins" {
  type    = list(string)
  default = ["*"]
}

variable "labels" {
  type = map(string)
  default = {
    system = "gha-rainfall"
    env    = "dev"
  }
}
