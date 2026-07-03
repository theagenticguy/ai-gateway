include "root" {
  path = find_in_parent_folders()
}

locals {
  common = read_terragrunt_config(find_in_parent_folders("common.hcl", "_env/common.hcl"))
  env    = read_terragrunt_config("env.hcl")
}

inputs = merge(
  local.common.locals,
  local.env.locals,
  {
    gateway_desired_count    = 2
    gateway_cpu              = 1024
    gateway_memory           = 2048
    autoscaling_min_capacity = 2
    autoscaling_max_capacity = 6
    enable_waf               = true
    single_nat_gateway       = false # prod: one NAT gateway per AZ for egress HA (ADR-003 addendum)
    certificate_arn          = ""    # Set to your ACM cert ARN
    cognito_domain_prefix    = "ai-gateway-prod"
    enable_jwt_auth          = false
  }
)
