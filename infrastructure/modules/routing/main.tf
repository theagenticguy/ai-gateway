terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.22"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.0"
    }
  }
}

# =============================================================================
# Routing — DynamoDB table for custom routing configs
# =============================================================================

data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# Custom Routing Configs Table
#
# PK: config_name (S) — unique config identifier (e.g. "my-ab-test")
# -----------------------------------------------------------------------------

resource "aws_dynamodb_table" "routing_configs" {
  count = var.enable_routing_api ? 1 : 0

  name         = "${var.project_name}-${var.environment}-routing-configs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "config_name"

  attribute {
    name = "config_name"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.routing[0].arn
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-routing-configs"
  })
}
