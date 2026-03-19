# Root terragrunt configuration
# All child modules inherit this

locals {
  env_config  = read_terragrunt_config(find_in_parent_folders("env.hcl"))
  environment = local.env_config.locals.environment
  aws_region  = local.env_config.locals.aws_region
}

# Remote state — each env gets its own state file
remote_state {
  backend = "s3"
  generate = {
    path      = "backend.tf"
    if_exists = "overwrite_terragrunt"
  }
  config = {
    bucket         = "ai-gateway-tfstate-${local.environment}"
    key            = "terraform.tfstate"
    region         = local.aws_region
    encrypt        = true
    dynamodb_table = "ai-gateway-tfstate-lock-${local.environment}"
  }
}

# Generate provider block
generate "provider" {
  path      = "provider.tf"
  if_exists = "overwrite_terragrunt"
  contents  = <<EOF
provider "aws" {
  region = "${local.aws_region}"
  default_tags {
    tags = {
      Project     = "ai-gateway"
      ManagedBy   = "terragrunt"
      Environment = "${local.environment}"
    }
  }
}
EOF
}

# Point to the Terraform module
terraform {
  source = "${get_repo_root()}/infrastructure"
}
