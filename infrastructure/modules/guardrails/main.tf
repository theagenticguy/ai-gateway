terraform {
  required_version = "~> 1.14"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.22"
    }
  }
}

# =============================================================================
# Guardrails — Bedrock Guardrails for content safety filtering
# =============================================================================
# ADR-017 (Option A): the guardrail runs in DETECT/LOG-ONLY mode by default.
# Every filter's input_action / output_action is set from local.filter_action,
# which resolves to "NONE" unless var.enforce_guardrails is true (then "BLOCK").
# In NONE mode the AWS ApplyGuardrail API still evaluates content and returns
# assessments, but the guardrail does not block or anonymize — agentgateway
# calls it inline and passes the request through. Flip enforce_guardrails per
# environment to turn specific filters into hard blocks without code changes.
#
# input_action / output_action accept "NONE" | "BLOCK" (PII filters also accept
# "ANONYMIZE"); confirmed against the aws_bedrock_guardrail v6.37 provider
# schema (content_policy_config.filters_config,
# sensitive_information_policy_config.pii_entities_config, word_policy_config).
# topic_policy_config.topics_config exposes no action field (topics are always
# DENY-typed), so topic detection cannot be made non-blocking at the filter
# level; topics are therefore only attached when enforcing (see below).

locals {
  # The action applied to every detect-capable filter.
  filter_action = var.enforce_guardrails ? "BLOCK" : "NONE"
  # PROMPT_ATTACK output is not a meaningful signal, so its output stays NONE
  # regardless of enforcement (matches the prior config's intent).
}

resource "aws_bedrock_guardrail" "this" {
  count = var.enable_guardrails ? 1 : 0

  name                      = "${var.project_name}-${var.environment}"
  blocked_input_messaging   = var.guardrail_blocked_message
  blocked_outputs_messaging = var.guardrail_blocked_message
  description               = "Content safety guardrail for ${var.project_name} (${var.environment}) — ${var.enforce_guardrails ? "ENFORCE (block)" : "DETECT/LOG-ONLY"}"

  # ---------------------------------------------------------------------------
  # Content policy — detect (or block) harmful content categories
  # ---------------------------------------------------------------------------

  content_policy_config {
    filters_config {
      type            = "HATE"
      input_strength  = var.content_filter_strength
      output_strength = var.content_filter_strength
      input_action    = local.filter_action
      output_action   = local.filter_action
    }
    filters_config {
      type            = "INSULTS"
      input_strength  = var.content_filter_strength
      output_strength = var.content_filter_strength
      input_action    = local.filter_action
      output_action   = local.filter_action
    }
    filters_config {
      type            = "SEXUAL"
      input_strength  = var.content_filter_strength
      output_strength = var.content_filter_strength
      input_action    = local.filter_action
      output_action   = local.filter_action
    }
    filters_config {
      type            = "VIOLENCE"
      input_strength  = var.content_filter_strength
      output_strength = var.content_filter_strength
      input_action    = local.filter_action
      output_action   = local.filter_action
    }
    filters_config {
      type            = "MISCONDUCT"
      input_strength  = var.content_filter_strength
      output_strength = var.content_filter_strength
      input_action    = local.filter_action
      output_action   = local.filter_action
    }
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
      input_action    = local.filter_action
      output_action   = "NONE"
    }
  }

  # ---------------------------------------------------------------------------
  # Sensitive information policy — detect (or block) PII
  # ---------------------------------------------------------------------------

  dynamic "sensitive_information_policy_config" {
    for_each = length(var.blocked_pii_types) > 0 ? [1] : []
    content {
      dynamic "pii_entities_config" {
        for_each = var.blocked_pii_types
        content {
          type          = pii_entities_config.value
          action        = local.filter_action
          input_action  = local.filter_action
          output_action = local.filter_action
        }
      }
    }
  }

  # ---------------------------------------------------------------------------
  # Topic policy — topics are always DENY-typed (no detect-only at the filter
  # level), so they are attached only when enforcing. In detect-only mode topic
  # coverage is intentionally off; content + PII filters carry detection.
  # ---------------------------------------------------------------------------

  dynamic "topic_policy_config" {
    for_each = var.enforce_guardrails && length(var.blocked_topics) > 0 ? [1] : []
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
  # Word policy — detect (or block) specific words/phrases
  # ---------------------------------------------------------------------------

  dynamic "word_policy_config" {
    for_each = length(var.blocked_words) > 0 ? [1] : []
    content {
      dynamic "words_config" {
        for_each = var.blocked_words
        content {
          text          = words_config.value
          input_action  = local.filter_action
          output_action = local.filter_action
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
