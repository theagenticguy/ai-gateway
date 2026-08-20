# SPDX-License-Identifier: Apache-2.0
provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = "ai-gateway"
      ManagedBy   = "terraform"
      Environment = var.environment
    }
  }
}
