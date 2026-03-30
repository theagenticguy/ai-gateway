terraform {
  required_version = "~> 1.14"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.22"
    }
  }
}

# =============================================================================
# AppConfig — feature flags and dynamic configuration for content scanner
# =============================================================================

locals {
  scanner_config_schema = jsonencode({
    "$schema" = "http://json-schema.org/draft-07/schema#"
    type      = "object"
    required  = ["enabled", "timeout_ms", "deny_on_block", "team_overrides"]
    properties = {
      enabled = {
        type = "boolean"
      }
      timeout_ms = {
        type    = "integer"
        minimum = 100
        maximum = 30000
      }
      deny_on_block = {
        type = "boolean"
      }
      team_overrides = {
        type = "object"
        additionalProperties = {
          type = "object"
          properties = {
            enabled = {
              type = "boolean"
            }
            timeout_ms = {
              type    = "integer"
              minimum = 100
              maximum = 30000
            }
            deny_on_block = {
              type = "boolean"
            }
          }
        }
      }
    }
    additionalProperties = false
  })
}

# -----------------------------------------------------------------------------
# Application
# -----------------------------------------------------------------------------

resource "aws_appconfig_application" "this" {
  count       = var.enable_appconfig ? 1 : 0
  name        = var.project_name
  description = "AppConfig application for ${var.project_name}"

  tags = merge(var.tags, {
    Name = "${var.project_name}-appconfig"
  })
}

# -----------------------------------------------------------------------------
# Environment
# -----------------------------------------------------------------------------

resource "aws_appconfig_environment" "this" {
  count          = var.enable_appconfig ? 1 : 0
  name           = var.environment
  description    = "${var.environment} environment for ${var.project_name}"
  application_id = aws_appconfig_application.this[0].id

  dynamic "monitor" {
    for_each = var.rollback_alarm_arn != "" ? [var.rollback_alarm_arn] : []
    content {
      alarm_arn = monitor.value
    }
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-appconfig-env"
  })
}

# -----------------------------------------------------------------------------
# Configuration Profile — scanner-config (freeform with JSON Schema validator)
# -----------------------------------------------------------------------------

resource "aws_appconfig_configuration_profile" "scanner" {
  count          = var.enable_appconfig ? 1 : 0
  application_id = aws_appconfig_application.this[0].id
  name           = "scanner-config"
  description    = "Content scanner feature flags and tuning parameters"
  location_uri   = "hosted"

  validator {
    type    = "JSON_SCHEMA"
    content = local.scanner_config_schema
  }

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-scanner-config"
  })
}

# -----------------------------------------------------------------------------
# Hosted Configuration Version — initial config
# -----------------------------------------------------------------------------

resource "aws_appconfig_hosted_configuration_version" "scanner" {
  count                    = var.enable_appconfig ? 1 : 0
  application_id           = aws_appconfig_application.this[0].id
  configuration_profile_id = aws_appconfig_configuration_profile.scanner[0].configuration_profile_id
  content_type             = "application/json"
  content                  = var.initial_scanner_config
  description              = "Initial scanner configuration managed by Terraform"
}

# -----------------------------------------------------------------------------
# Deployment Strategy — linear 10% every 1 min, 5 min bake
# -----------------------------------------------------------------------------

resource "aws_appconfig_deployment_strategy" "linear_10pct" {
  count                          = var.enable_appconfig ? 1 : 0
  name                           = "${var.project_name}-${var.environment}-linear-10pct"
  description                    = "Linear 10% per minute with 5 min bake time"
  deployment_duration_in_minutes = 10
  growth_factor                  = 10
  growth_type                    = "LINEAR"
  final_bake_time_in_minutes     = 5
  replicate_to                   = "NONE"

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-linear-10pct"
  })
}

# -----------------------------------------------------------------------------
# Initial Deployment
# -----------------------------------------------------------------------------

resource "aws_appconfig_deployment" "scanner" {
  count                    = var.enable_appconfig ? 1 : 0
  application_id           = aws_appconfig_application.this[0].id
  configuration_profile_id = aws_appconfig_configuration_profile.scanner[0].configuration_profile_id
  configuration_version    = aws_appconfig_hosted_configuration_version.scanner[0].version_number
  deployment_strategy_id   = aws_appconfig_deployment_strategy.linear_10pct[0].id
  environment_id           = aws_appconfig_environment.this[0].environment_id
  description              = "Initial scanner config deployment"

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-scanner-deployment"
  })
}
