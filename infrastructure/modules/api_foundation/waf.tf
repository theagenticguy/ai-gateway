# =============================================================================
# WAF — regional Web ACL on the control-plane stage (ADR-016)
# =============================================================================
# The inference path's WAF lives on the ALB; the admin plane was previously
# unprotected (the admin_api module skipped CKV2_AWS_29 as "low-traffic"). A
# rate-based rule + AWS managed common/known-bad-inputs sets close that gap.

resource "aws_wafv2_web_acl" "control" {
  count       = var.enable_api_foundation && var.waf_enabled ? 1 : 0
  name        = "${local.name}-waf"
  scope       = "REGIONAL"
  description = "Control-plane API protection"

  default_action {
    allow {}
  }

  rule {
    name     = "rate-limit"
    priority = 1
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name}-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "common-rule-set"
    priority = 2
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name}-common"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "known-bad-inputs"
    priority = 3
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name}-known-bad"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.name}-waf"
    sampled_requests_enabled   = true
  }
}

resource "aws_wafv2_web_acl_association" "control" {
  count        = var.enable_api_foundation && var.waf_enabled ? 1 : 0
  resource_arn = aws_api_gateway_stage.control[0].arn
  web_acl_arn  = aws_wafv2_web_acl.control[0].arn
}
