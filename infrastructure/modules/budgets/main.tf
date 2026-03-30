terraform {
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
# Budgets — DynamoDB tables for budget definitions and usage tracking
# =============================================================================

data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# Budget Definitions Table
#
# PK: budget_id (S) — unique budget identifier
# SK: scope (S)     — scope type (e.g., "team", "project", "user")
# GSI: scope-index  — lookup budgets by scope + scope_id
# -----------------------------------------------------------------------------

resource "aws_dynamodb_table" "budgets" {
  count = var.enable_budgets ? 1 : 0

  name         = "${var.project_name}-${var.environment}-budgets"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "budget_id"
  range_key    = "scope"

  attribute {
    name = "budget_id"
    type = "S"
  }

  attribute {
    name = "scope"
    type = "S"
  }

  attribute {
    name = "scope_id"
    type = "S"
  }

  global_secondary_index {
    name            = "scope-index"
    hash_key        = "scope"
    range_key       = "scope_id"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.budgets[0].arn
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-budgets"
  })
}

# -----------------------------------------------------------------------------
# Usage Tracking Table
#
# PK: scope_id (S)    — identifies the entity being tracked
# SK: period_date (S)  — date or period key (e.g., "2026-03-21", "2026-03")
# GSI: period-index    — query usage across all scopes for a given period
# TTL: expires_at      — automatic cleanup of old usage records
# -----------------------------------------------------------------------------

resource "aws_dynamodb_table" "usage" {
  count = var.enable_budgets ? 1 : 0

  name         = "${var.project_name}-${var.environment}-usage"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "scope_id"
  range_key    = "period_date"

  attribute {
    name = "scope_id"
    type = "S"
  }

  attribute {
    name = "period_date"
    type = "S"
  }

  global_secondary_index {
    name            = "period-index"
    hash_key        = "period_date"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.budgets[0].arn
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-usage"
  })
}
