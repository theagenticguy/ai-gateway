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
# Networking + Edge — VPC, ALB, WAF
# =============================================================================

# ------------------------------------------------------------------
# VPC
# ------------------------------------------------------------------

module "vpc" {
  #checkov:skip=CKV_TF_1:Registry modules pinned by version; commit hash not applicable
  source  = "terraform-aws-modules/vpc/aws"
  version = "6.6.0"

  name = "${var.project_name}-${var.environment}"
  cidr = var.vpc_cidr

  azs             = var.azs
  public_subnets  = [cidrsubnet(var.vpc_cidr, 8, 1), cidrsubnet(var.vpc_cidr, 8, 2)]
  private_subnets = [cidrsubnet(var.vpc_cidr, 8, 10), cidrsubnet(var.vpc_cidr, 8, 20)]

  enable_nat_gateway   = true
  single_nat_gateway   = var.single_nat_gateway
  enable_dns_hostnames = true
  enable_dns_support   = true
}

# ------------------------------------------------------------------
# VPC Endpoints
# ------------------------------------------------------------------

resource "aws_security_group" "vpc_endpoints" {
  name_prefix = "${var.project_name}-${var.environment}-vpce-"
  description = "Security group for VPC interface endpoints"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description = "HTTPS from private subnets"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = module.vpc.private_subnets_cidr_blocks
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-vpce"
  }
}

# S3 Gateway endpoint (free)
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.vpc.vpc_id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = module.vpc.private_route_table_ids

  tags = {
    Name = "${var.project_name}-${var.environment}-s3"
  }
}

# Interface endpoints
locals {
  interface_endpoints = {
    ecr_api        = "com.amazonaws.${var.aws_region}.ecr.api"
    ecr_dkr        = "com.amazonaws.${var.aws_region}.ecr.dkr"
    logs           = "com.amazonaws.${var.aws_region}.logs"
    secretsmanager = "com.amazonaws.${var.aws_region}.secretsmanager"
  }
}

resource "aws_vpc_endpoint" "interface" {
  for_each = local.interface_endpoints

  vpc_id              = module.vpc.vpc_id
  service_name        = each.value
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = module.vpc.private_subnets
  security_group_ids  = [aws_security_group.vpc_endpoints.id]

  tags = {
    Name = "${var.project_name}-${var.environment}-${each.key}"
  }
}

# ------------------------------------------------------------------
# ALB
# ------------------------------------------------------------------

module "alb" {
  #checkov:skip=CKV_TF_1:Registry modules pinned by version; commit hash not applicable
  source  = "terraform-aws-modules/alb/aws"
  version = "10.5.0"

  name               = "${var.project_name}-${var.environment}"
  load_balancer_type = "application"
  vpc_id             = module.vpc.vpc_id
  subnets            = module.vpc.public_subnets

  security_group_ingress_rules = {
    all_https = {
      from_port   = 443
      to_port     = 443
      ip_protocol = "tcp"
      cidr_ipv4   = "0.0.0.0/0"
    }
    all_http = {
      from_port   = 80
      to_port     = 80
      ip_protocol = "tcp"
      cidr_ipv4   = "0.0.0.0/0"
    }
  }

  security_group_egress_rules = {
    all = {
      ip_protocol = "-1"
      cidr_ipv4   = module.vpc.vpc_cidr_block
    }
  }

  listeners = merge(
    # HTTP listener — always created
    {
      http = {
        port     = 80
        protocol = "HTTP"

        # Redirect to HTTPS when a certificate is available, otherwise forward to gateway
        forward = var.certificate_arn == "" ? {
          target_group_key = "gateway"
        } : null

        redirect = var.certificate_arn != "" ? {
          port        = "443"
          protocol    = "HTTPS"
          status_code = "HTTP_301"
        } : null
      }
    },
    # HTTPS listener — only when a certificate ARN is provided
    var.certificate_arn != "" && !var.enable_jwt_auth ? {
      https = {
        port            = 443
        protocol        = "HTTPS"
        ssl_policy      = "ELBSecurityPolicy-TLS13-1-2-2021-06"
        certificate_arn = var.certificate_arn

        forward = {
          target_group_key = "gateway"
        }
      }
    } : {}
  )

  target_groups = {
    gateway = {
      backend_protocol = "HTTP"
      backend_port     = 8787
      target_type      = "ip"

      health_check = {
        enabled             = true
        path                = "/"
        port                = "8787"
        protocol            = "HTTP"
        healthy_threshold   = 2
        unhealthy_threshold = 3
        interval            = 15
        timeout             = 5
        matcher             = "200"
      }

      # ECS manages target registration
      create_attachment = false

      deregistration_delay = 30

      stickiness = {
        enabled = false
        type    = "lb_cookie"
      }
    }
  }
}

# ------------------------------------------------------------------
# WAF
# ------------------------------------------------------------------

resource "aws_wafv2_web_acl" "alb" {
  #checkov:skip=CKV2_AWS_31:WAF logging is configured via aws_wafv2_web_acl_logging_configuration.alb; checkov cannot resolve count-based graph edges
  count = var.enable_waf ? 1 : 0

  name        = "${var.project_name}-${var.environment}"
  description = "WAF rules for ${var.project_name} ALB"
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 1

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
      metric_name                = "${var.project_name}-common-rules"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesAmazonIpReputationList"
    priority = 2

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAmazonIpReputationList"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-ip-reputation"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "RateLimitPerIP"
    priority = 3

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 2000
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesKnownBadInputsRuleSet"
    priority = 4

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
      metric_name                = "${var.project_name}-known-bad-inputs"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.project_name}-waf"
    sampled_requests_enabled   = true
  }
}

# ------------------------------------------------------------------
# WAF Logging
# ------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "waf" {
  #checkov:skip=CKV_AWS_158:KMS encryption planned for prod
  #checkov:skip=CKV_AWS_338:365-day retention planned for prod
  count = var.enable_waf ? 1 : 0

  name              = "aws-waf-logs-${var.project_name}-${var.environment}"
  retention_in_days = 365
  kms_key_id        = var.waf_log_kms_key_arn
}

resource "aws_wafv2_web_acl_logging_configuration" "alb" {
  count = var.enable_waf ? 1 : 0

  log_destination_configs = [aws_cloudwatch_log_group.waf[0].arn]
  resource_arn            = aws_wafv2_web_acl.alb[0].arn
}

resource "aws_wafv2_web_acl_association" "alb" {
  count = var.enable_waf ? 1 : 0

  resource_arn = module.alb.arn
  web_acl_arn  = aws_wafv2_web_acl.alb[0].arn
}
