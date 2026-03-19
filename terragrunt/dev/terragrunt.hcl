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
    gateway_cpu              = 512
    gateway_memory           = 1024
    autoscaling_min_capacity = 1
    autoscaling_max_capacity = 3
    enable_waf               = false
    certificate_arn          = ""
    cognito_domain_prefix    = "ai-gateway-dev"
    enable_jwt_auth          = false
  }
)
