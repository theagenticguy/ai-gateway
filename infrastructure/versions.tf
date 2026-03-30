terraform {
  required_version = "~> 1.14"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.22"
    }
  }
  # Backend is configured per-environment via -backend-config
  backend "s3" {}
}
