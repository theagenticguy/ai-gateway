# =============================================================================
# Inspector — Continuous ECR vulnerability scanning
# =============================================================================
# Enables Amazon Inspector enhanced scanning for ECR repositories.
# Unlike basic ECR scan-on-push, Inspector continuously re-evaluates
# images against newly published CVEs — not just at push time.
# =============================================================================

terraform {
  required_version = "~> 1.14"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.22"
    }
  }
}

data "aws_caller_identity" "current" {}

# ------------------------------------------------------------------
# Enable Inspector for ECR scanning in this account/region
# ------------------------------------------------------------------

resource "aws_inspector2_enabler" "ecr" {
  count = var.enable_inspector ? 1 : 0

  account_ids    = [data.aws_caller_identity.current.account_id]
  resource_types = ["ECR"]
}

# ------------------------------------------------------------------
# Configure enhanced scanning (continuous for prod, on-push for dev)
# ------------------------------------------------------------------

resource "aws_ecr_registry_scanning_configuration" "this" {
  count = var.enable_inspector ? 1 : 0

  scan_type = "ENHANCED"

  rule {
    scan_frequency = var.environment == "prod" ? "CONTINUOUS_SCAN" : "SCAN_ON_PUSH"

    repository_filter {
      filter      = "${var.project_name}-*"
      filter_type = "WILDCARD"
    }
  }

  depends_on = [aws_inspector2_enabler.ecr]
}
