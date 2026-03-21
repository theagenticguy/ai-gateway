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

  project_name        = var.project_name
  environment         = var.environment
  aws_region          = var.aws_region
  account_id          = data.aws_caller_identity.current.account_id
  enable_cost_widgets = var.enable_cost_attribution
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
