# =============================================================================
# moved {} blocks for Terraform module restructure
#
# Maps every resource from the flat root module to its new location inside
# local modules. Safe to remove after the first successful apply.
# =============================================================================

# -----------------------------------------------------------------------------
# Observability: cloudwatch.tf → modules/observability/
# -----------------------------------------------------------------------------

moved {
  from = aws_kms_key.logs
  to   = module.observability.aws_kms_key.logs
}

moved {
  from = aws_kms_alias.logs
  to   = module.observability.aws_kms_alias.logs
}

moved {
  from = aws_cloudwatch_log_group.gateway
  to   = module.observability.aws_cloudwatch_log_group.gateway
}

moved {
  from = aws_cloudwatch_log_group.otel
  to   = module.observability.aws_cloudwatch_log_group.otel
}

# dashboard.tf → modules/observability/

moved {
  from = aws_cloudwatch_query_definition.requests_per_hour
  to   = module.observability.aws_cloudwatch_query_definition.requests_per_hour
}

moved {
  from = aws_cloudwatch_query_definition.error_rate
  to   = module.observability.aws_cloudwatch_query_definition.error_rate
}

moved {
  from = aws_cloudwatch_query_definition.latency_percentiles
  to   = module.observability.aws_cloudwatch_query_definition.latency_percentiles
}

moved {
  from = aws_cloudwatch_query_definition.requests_by_endpoint
  to   = module.observability.aws_cloudwatch_query_definition.requests_by_endpoint
}

moved {
  from = aws_cloudwatch_dashboard.main
  to   = module.observability.aws_cloudwatch_dashboard.main
}

# -----------------------------------------------------------------------------
# Networking: vpc.tf + alb.tf + waf.tf → modules/networking/
# -----------------------------------------------------------------------------

moved {
  from = module.vpc
  to   = module.networking.module.vpc
}

moved {
  from = aws_security_group.vpc_endpoints
  to   = module.networking.aws_security_group.vpc_endpoints
}

moved {
  from = aws_vpc_endpoint.s3
  to   = module.networking.aws_vpc_endpoint.s3
}

moved {
  from = aws_vpc_endpoint.interface
  to   = module.networking.aws_vpc_endpoint.interface
}

moved {
  from = module.alb
  to   = module.networking.module.alb
}

moved {
  from = aws_wafv2_web_acl.alb
  to   = module.networking.aws_wafv2_web_acl.alb
}

moved {
  from = aws_cloudwatch_log_group.waf
  to   = module.networking.aws_cloudwatch_log_group.waf
}

moved {
  from = aws_wafv2_web_acl_logging_configuration.alb
  to   = module.networking.aws_wafv2_web_acl_logging_configuration.alb
}

moved {
  from = aws_wafv2_web_acl_association.alb
  to   = module.networking.aws_wafv2_web_acl_association.alb
}

# -----------------------------------------------------------------------------
# Auth: cognito.tf + alb_auth.tf → modules/auth/
# -----------------------------------------------------------------------------

moved {
  from = aws_cognito_user_pool.gateway
  to   = module.auth.aws_cognito_user_pool.gateway
}

moved {
  from = aws_cognito_resource_server.gateway
  to   = module.auth.aws_cognito_resource_server.gateway
}

moved {
  from = aws_cognito_user_pool_client.gateway_m2m
  to   = module.auth.aws_cognito_user_pool_client.gateway_m2m
}

moved {
  from = aws_cognito_user_pool_domain.gateway
  to   = module.auth.aws_cognito_user_pool_domain.gateway
}

moved {
  from = aws_lb_listener.https_jwt
  to   = module.auth.aws_lb_listener.https_jwt
}

# -----------------------------------------------------------------------------
# Compute: ecs.tf + ecr.tf + iam.tf + secrets.tf → modules/compute/
# -----------------------------------------------------------------------------

# ECR + KMS
moved {
  from = aws_kms_key.ecr
  to   = module.compute.aws_kms_key.ecr
}

moved {
  from = aws_kms_alias.ecr
  to   = module.compute.aws_kms_alias.ecr
}

moved {
  from = aws_ecr_repository.gateway
  to   = module.compute.aws_ecr_repository.gateway
}

moved {
  from = aws_ecr_lifecycle_policy.gateway
  to   = module.compute.aws_ecr_lifecycle_policy.gateway
}

# Secrets Manager + KMS
moved {
  from = aws_kms_key.secrets
  to   = module.compute.aws_kms_key.secrets
}

moved {
  from = aws_kms_alias.secrets
  to   = module.compute.aws_kms_alias.secrets
}

moved {
  from = aws_secretsmanager_secret.secrets
  to   = module.compute.aws_secretsmanager_secret.secrets
}

moved {
  from = aws_secretsmanager_secret_version.secrets
  to   = module.compute.aws_secretsmanager_secret_version.secrets
}

# IAM
moved {
  from = aws_iam_role.ecs_task_execution
  to   = module.compute.aws_iam_role.ecs_task_execution
}

moved {
  from = aws_iam_role_policy_attachment.ecs_task_execution_managed
  to   = module.compute.aws_iam_role_policy_attachment.ecs_task_execution_managed
}

moved {
  from = aws_iam_role_policy.ecs_task_execution_secrets
  to   = module.compute.aws_iam_role_policy.ecs_task_execution_secrets
}

moved {
  from = aws_iam_role.ecs_task
  to   = module.compute.aws_iam_role.ecs_task
}

moved {
  from = aws_iam_role_policy.ecs_task_bedrock
  to   = module.compute.aws_iam_role_policy.ecs_task_bedrock
}

moved {
  from = aws_iam_role_policy.ecs_task_observability
  to   = module.compute.aws_iam_role_policy.ecs_task_observability
}

# ECS
moved {
  from = module.ecs_cluster
  to   = module.compute.module.ecs_cluster
}

moved {
  from = module.ecs_service
  to   = module.compute.module.ecs_service
}
