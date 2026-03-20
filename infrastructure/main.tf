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

  project_name = var.project_name
  environment  = var.environment
  aws_region   = var.aws_region
  account_id   = data.aws_caller_identity.current.account_id
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
}
