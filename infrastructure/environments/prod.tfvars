environment              = "prod"
aws_region               = "us-east-1"
gateway_desired_count    = 2
gateway_cpu              = 1024
gateway_memory           = 2048
autoscaling_min_capacity = 2
autoscaling_max_capacity = 6
enable_waf               = true
# Secure by default: prod runs JWT-authenticated. Set BOTH of the following to
# real values before applying — the guards.tf precondition fails the plan if
# enable_jwt_auth = true and either is empty.
certificate_arn          = "" # REQUIRED: your ACM certificate ARN for the HTTPS listener
cognito_user_pool_id     = "" # REQUIRED: Cognito User Pool ID (JWT issuer / JWKS source)
cognito_domain_prefix    = "ai-gateway-prod"
enable_jwt_auth          = true
