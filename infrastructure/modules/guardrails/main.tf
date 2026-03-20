terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

# =============================================================================
# Guardrails — Bedrock Guardrails for content safety filtering
# =============================================================================

resource "aws_bedrock_guardrail" "this" {
  count = var.enable_guardrails ? 1 : 0

  name                      = "${var.project_name}-${var.environment}"
  blocked_input_messaging   = var.guardrail_blocked_message
  blocked_outputs_messaging = var.guardrail_blocked_message
  description               = "Content safety guardrail for ${var.project_name} (${var.environment})"

  # ---------------------------------------------------------------------------
  # Content policy — block harmful content categories
  # ---------------------------------------------------------------------------

  content_policy_config {
    filters_config {
      type            = "HATE"
      input_strength  = var.content_filter_strength
      output_strength = var.content_filter_strength
    }
    filters_config {
      type            = "INSULTS"
      input_strength  = var.content_filter_strength
      output_strength = var.content_filter_strength
    }
    filters_config {
      type            = "SEXUAL"
      input_strength  = var.content_filter_strength
      output_strength = var.content_filter_strength
    }
    filters_config {
      type            = "VIOLENCE"
      input_strength  = var.content_filter_strength
      output_strength = var.content_filter_strength
    }
    filters_config {
      type            = "MISCONDUCT"
      input_strength  = var.content_filter_strength
      output_strength = var.content_filter_strength
    }
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
  }

  # ---------------------------------------------------------------------------
  # Sensitive information policy — block PII leakage
  # ---------------------------------------------------------------------------

  dynamic "sensitive_information_policy_config" {
    for_each = length(var.blocked_pii_types) > 0 ? [1] : []
    content {
      dynamic "pii_entities_config" {
        for_each = var.blocked_pii_types
        content {
          type   = pii_entities_config.value
          action = "BLOCK"
        }
      }
    }
  }

  # ---------------------------------------------------------------------------
  # Topic policy — block configurable topics
  # ---------------------------------------------------------------------------

  dynamic "topic_policy_config" {
    for_each = length(var.blocked_topics) > 0 ? [1] : []
    content {
      dynamic "topics_config" {
        for_each = var.blocked_topics
        content {
          name       = topics_config.value.name
          type       = "DENY"
          definition = topics_config.value.definition
          examples   = lookup(topics_config.value, "examples", [])
        }
      }
    }
  }

  # ---------------------------------------------------------------------------
  # Word policy — block specific words/phrases
  # ---------------------------------------------------------------------------

  dynamic "word_policy_config" {
    for_each = length(var.blocked_words) > 0 ? [1] : []
    content {
      dynamic "words_config" {
        for_each = var.blocked_words
        content {
          text = words_config.value
        }
      }
    }
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-guardrail"
  }
}

# -----------------------------------------------------------------------------
# Guardrail Version — immutable published version for production use
# -----------------------------------------------------------------------------

resource "aws_bedrock_guardrail_version" "this" {
  count = var.enable_guardrails ? 1 : 0

  guardrail_arn = aws_bedrock_guardrail.this[0].guardrail_arn
  description   = "Managed by Terraform"
  skip_destroy  = true
}
