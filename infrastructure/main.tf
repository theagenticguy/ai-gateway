# =============================================================================
# Root Module — wires together networking, auth, compute, and observability
# =============================================================================

data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" {
  state = "available"
}

# -----------------------------------------------------------------------------
# Observability (creates log groups + KMS — needed by other modules)
# -----------------------------------------------------------------------------

module "observability" {
  source = "./modules/observability"

  project_name         = var.project_name
  environment          = var.environment
  aws_region           = var.aws_region
  account_id           = data.aws_caller_identity.current.account_id
  enable_cost_widgets  = var.enable_cost_attribution
  enable_cache_widgets = var.enable_cache

  # Alarm configuration
  alarm_sns_topic_arns          = var.alarm_sns_topic_arns
  budget_limit_daily_usd        = var.budget_limit_daily_usd
  budget_alarm_threshold_pct    = var.budget_alarm_threshold_pct
  error_rate_threshold_pct      = var.error_rate_threshold_pct
  error_rate_evaluation_minutes = var.error_rate_evaluation_minutes
  p99_latency_threshold_ms      = var.p99_latency_threshold_ms
  latency_evaluation_minutes    = var.latency_evaluation_minutes
  provider_down_minutes         = var.provider_down_minutes
}

# -----------------------------------------------------------------------------
# Networking (needs logs KMS for WAF log group encryption)
# -----------------------------------------------------------------------------

module "networking" {
  source = "./modules/networking"

  project_name    = var.project_name
  environment     = var.environment
  aws_region      = var.aws_region
  vpc_cidr        = var.vpc_cidr
  azs             = slice(data.aws_availability_zones.available.names, 0, 2)
  certificate_arn = var.certificate_arn
  enable_waf      = var.enable_waf
  enable_jwt_auth = var.enable_jwt_auth

  waf_log_kms_key_arn = module.observability.logs_kms_key_arn
}

# -----------------------------------------------------------------------------
# Auth (needs ALB ARN + target group from networking)
# -----------------------------------------------------------------------------

module "auth" {
  source = "./modules/auth"

  project_name          = var.project_name
  environment           = var.environment
  aws_region            = var.aws_region
  cognito_domain_prefix = var.cognito_domain_prefix
  cognito_user_pool_id  = var.cognito_user_pool_id
  enable_jwt_auth       = var.enable_jwt_auth
  certificate_arn       = var.certificate_arn

  alb_arn                      = module.networking.alb_arn
  alb_target_group_gateway_arn = module.networking.alb_target_group_gateway_arn

  # Identity Center / SSO (D.1)
  identity_providers = var.identity_providers
  enable_user_auth   = var.enable_user_auth
  callback_urls      = var.callback_urls
  logout_urls        = var.logout_urls
  group_mapping      = var.group_mapping
}

# -----------------------------------------------------------------------------
# Clients (per-team Cognito app clients -- only created if client_configs is set)
# -----------------------------------------------------------------------------

module "clients" {
  source = "./modules/clients"
  count  = length(var.client_configs) > 0 ? 1 : 0

  project_name                      = var.project_name
  environment                       = var.environment
  user_pool_id                      = module.auth.cognito_user_pool_id
  resource_server_scope_identifiers = module.auth.resource_server_scope_identifiers
  client_configs                    = var.client_configs
}

# -----------------------------------------------------------------------------
# Admin API (API Gateway REST API with Cognito authorizer for admin endpoints)
# -----------------------------------------------------------------------------

module "admin_api" {
  source = "./modules/admin_api"
  count  = var.enable_admin_api ? 1 : 0

  project_name          = var.project_name
  environment           = var.environment
  enable_admin_api      = var.enable_admin_api
  cognito_user_pool_arn = module.auth.cognito_user_pool_arn
}

# -----------------------------------------------------------------------------
# Team Registration (Lambda + DynamoDB for self-service team onboarding)
# -----------------------------------------------------------------------------

module "team_registration" {
  source = "./modules/team_registration"
  count  = var.enable_admin_api ? 1 : 0

  project_name = var.project_name
  environment  = var.environment
  aws_region   = var.aws_region

  enable_team_registration = true

  cognito_user_pool_id   = module.auth.cognito_user_pool_id
  cognito_user_pool_arn  = module.auth.cognito_user_pool_arn
  cognito_token_endpoint = module.auth.cognito_token_endpoint

  # Budget tables (from budgets module, if enabled)
  budgets_table_name = var.enable_budgets ? module.budgets[0].budgets_table_name : "gateway-budgets"
  budgets_table_arn  = var.enable_budgets ? module.budgets[0].budgets_table_arn : ""
  usage_table_name   = var.enable_budgets ? module.budgets[0].usage_table_name : "gateway-usage"
  usage_table_arn    = var.enable_budgets ? module.budgets[0].usage_table_arn : ""
}

# -----------------------------------------------------------------------------
# Routing (Lambda + DynamoDB for dynamic routing config management)
# -----------------------------------------------------------------------------

module "routing" {
  source = "./modules/routing"
  count  = var.enable_admin_api ? 1 : 0

  project_name       = var.project_name
  environment        = var.environment
  enable_routing_api = true
}

# -----------------------------------------------------------------------------
# Compute (needs VPC subnets, ALB SG + target group, log group names)
# -----------------------------------------------------------------------------

module "compute" {
  source = "./modules/compute"

  project_name             = var.project_name
  environment              = var.environment
  aws_region               = var.aws_region
  portkey_image            = var.portkey_image
  gateway_desired_count    = var.gateway_desired_count
  gateway_cpu              = var.gateway_cpu
  gateway_memory           = var.gateway_memory
  autoscaling_min_capacity = var.autoscaling_min_capacity
  autoscaling_max_capacity = var.autoscaling_max_capacity
  account_id               = data.aws_caller_identity.current.account_id

  private_subnets                     = module.networking.private_subnets
  alb_security_group_id               = module.networking.alb_security_group_id
  alb_target_group_gateway_arn        = module.networking.alb_target_group_gateway_arn
  alb_arn_suffix                      = module.networking.alb_arn_suffix
  alb_target_group_gateway_arn_suffix = module.networking.alb_target_group_gateway_arn_suffix

  gateway_log_group_name = module.observability.gateway_log_group_name
  otel_log_group_name    = module.observability.otel_log_group_name
  otel_config_content    = file("${path.module}/otel-config.yaml")

  # Routing
  portkey_routing_configs = var.enable_provider_fallback ? {
    for name, config in var.routing_configs : name => base64encode(config)
  } : {}

  # Cache
  cache_enabled = var.enable_cache
  redis_url     = var.enable_cache ? module.cache.redis_connection_url : ""

  # Webhook URLs for pre-request hooks
  budget_enforcement_webhook_url = var.enable_budgets ? module.budgets[0].function_url : ""
  content_scanner_webhook_url    = var.enable_content_scanner ? module.content_scanner.function_url : ""
}

# -----------------------------------------------------------------------------
# Cost Attribution (Lambda pipeline: gateway logs -> CloudWatch custom metrics)
# -----------------------------------------------------------------------------

module "cost_attribution" {
  source = "./modules/cost_attribution"

  project_name            = var.project_name
  environment             = var.environment
  aws_region              = var.aws_region
  account_id              = data.aws_caller_identity.current.account_id
  enable_cost_attribution = var.enable_cost_attribution
  gateway_log_group_name  = module.observability.gateway_log_group_name
  gateway_log_group_arn   = module.observability.gateway_log_group_arn

  # E.6: Budget alerts integration
  usage_table                 = var.enable_budgets ? module.budgets[0].usage_table_name : ""
  budgets_table               = var.enable_budgets ? module.budgets[0].budgets_table_name : ""
  budget_alerts_sns_topic_arn = var.enable_budgets ? module.budgets[0].budget_alerts_topic_arn : ""
}

# Content Scanner (Lambda: PII redaction + prompt injection detection)
# -----------------------------------------------------------------------------

module "content_scanner" {
  source = "./modules/content_scanner"

  project_name           = var.project_name
  environment            = var.environment
  aws_region             = var.aws_region
  account_id             = data.aws_caller_identity.current.account_id
  enable_content_scanner = var.enable_content_scanner
  default_pii_mode       = var.content_scanner_default_pii_mode
  default_injection_mode = var.content_scanner_default_injection_mode

  # AppConfig feature flag path (hot-path toggle)
  appconfig_path = var.enable_appconfig ? module.appconfig.appconfig_resource_path : ""
}

# =============================================================================
# AppConfig — Feature flags for content scanner and future toggles
# =============================================================================

module "appconfig" {
  source = "./modules/appconfig"

  enable_appconfig   = var.enable_appconfig
  project_name       = var.project_name
  environment        = var.environment
  rollback_alarm_arn = "" # TODO: Wire CloudWatch alarm for scanner errors
  tags               = {}
}

# Guardrails (Bedrock content safety filtering)
# -----------------------------------------------------------------------------

module "guardrails" {
  source = "./modules/guardrails"

  project_name = var.project_name
  environment  = var.environment

  enable_guardrails       = var.enable_guardrails
  content_filter_strength = var.guardrails_content_filter_strength
  blocked_topics          = var.guardrails_blocked_topics
  blocked_words           = var.guardrails_blocked_words
}

# -----------------------------------------------------------------------------
# Cache (ElastiCache Redis for response caching)
# -----------------------------------------------------------------------------

module "cache" {
  source = "./modules/cache"

  project_name    = var.project_name
  environment     = var.environment
  enable_cache    = var.enable_cache
  cache_node_type = var.cache_node_type

  private_subnet_ids    = module.networking.private_subnets
  vpc_id                = module.networking.vpc_id
  ecs_security_group_id = module.compute.ecs_security_group_id
}

# -----------------------------------------------------------------------------
# Budgets (DynamoDB tables for budget definitions and usage tracking)
# -----------------------------------------------------------------------------

module "budgets" {
  source = "./modules/budgets"
  count  = var.enable_budgets ? 1 : 0

  project_name   = var.project_name
  environment    = var.environment
  enable_budgets = var.enable_budgets
}

# -----------------------------------------------------------------------------
# Chargeback Reports (Step Functions + Lambda for monthly cost reports)
# -----------------------------------------------------------------------------

module "chargeback" {
  source = "./modules/chargeback"
  count  = var.enable_chargeback && var.enable_budgets ? 1 : 0

  project_name = var.project_name
  environment  = var.environment
  aws_region   = var.aws_region
  account_id   = data.aws_caller_identity.current.account_id

  enable_chargeback  = var.enable_chargeback
  usage_table_name   = module.budgets[0].usage_table_name
  usage_table_arn    = module.budgets[0].usage_table_arn
  budgets_table_name = module.budgets[0].budgets_table_name
  budgets_table_arn  = module.budgets[0].budgets_table_arn
  sns_topic_arn      = module.observability.alarm_topic_arns[0]
}

# -----------------------------------------------------------------------------
# Audit Log (Firehose -> S3 Parquet pipeline for compliance audit trail)
# -----------------------------------------------------------------------------

module "audit_log" {
  source = "./modules/audit_log"
  count  = var.enable_audit_log ? 1 : 0

  project_name     = var.project_name
  environment      = var.environment
  aws_region       = var.aws_region
  enable_audit_log = var.enable_audit_log
}

# -----------------------------------------------------------------------------
# Inspector (Amazon Inspector enhanced ECR scanning — continuous CVE monitoring)
# -----------------------------------------------------------------------------

module "inspector" {
  source = "./modules/inspector"

  project_name     = var.project_name
  environment      = var.environment
  enable_inspector = var.enable_inspector
}
