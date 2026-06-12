environment              = "dev"
aws_region               = "us-east-1"
gateway_desired_count    = 2
gateway_cpu              = 512
gateway_memory           = 1024
autoscaling_min_capacity = 1
autoscaling_max_capacity = 3
enable_waf               = false
certificate_arn          = ""
cognito_domain_prefix    = "ai-gateway-dev"
# dev runs unauthenticated BY CHOICE so the no-cert local smoke path still plans.
# This is a deliberate opt-out of the secure default (enable_jwt_auth = true),
# NOT the recommended posture — prod.tfvars models the secure default. To make
# dev secure too, set enable_jwt_auth = true and provide certificate_arn +
# cognito_user_pool_id (see guards.tf precondition + docs/admin-guide/security.md).
enable_jwt_auth = false
