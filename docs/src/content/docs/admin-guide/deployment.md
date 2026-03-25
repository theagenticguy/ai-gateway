---
title: Deployment
description: First-time setup, Terraform module structure, and rolling deployments.
sidebar:
  order: 2
---
## Prerequisites

Before deploying the AI Gateway, ensure the following are in place:

| Prerequisite | Description |
|---|---|
| AWS Account | An AWS account with permissions to create VPC, ECS, ALB, Cognito, KMS, Secrets Manager, and CloudWatch resources |
| Terraform >= 1.9 | Required version specified in `versions.tf` |
| AWS Provider ~> 6.22 | Required for ALB JWT validation support |
| S3 Bucket | One per environment for Terraform state (e.g., `ai-gateway-tfstate-dev`) |
| DynamoDB Table | One per environment for state locking (e.g., `ai-gateway-tfstate-lock-dev`) |
| ACM Certificate | TLS certificate for the ALB HTTPS listener (optional for dev, required for prod) |
| AWS CLI | Configured with appropriate credentials |

## First-Time Setup Checklist

1. **Create the S3 state bucket** for your target environment:

    ```bash
    aws s3api create-bucket \
      --bucket ai-gateway-tfstate-dev \
      --region us-east-1

    aws s3api put-bucket-versioning \
      --bucket ai-gateway-tfstate-dev \
      --versioning-configuration Status=Enabled

    aws s3api put-bucket-encryption \
      --bucket ai-gateway-tfstate-dev \
      --server-side-encryption-configuration \
        '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms"}}]}'
    ```

2. **Create the DynamoDB lock table**:

    ```bash
    aws dynamodb create-table \
      --table-name ai-gateway-tfstate-lock-dev \
      --attribute-definitions AttributeName=LockID,AttributeType=S \
      --key-schema AttributeName=LockID,KeyType=HASH \
      --billing-mode PAY_PER_REQUEST \
      --region us-east-1
    ```

3. **Request or import an ACM certificate** for your domain (if using HTTPS):

    ```bash
    aws acm request-certificate \
      --domain-name gateway.example.com \
      --validation-method DNS \
      --region us-east-1
    ```

4. **Set provider API keys** in Secrets Manager after the first apply (the secrets are created with placeholder values):

    ```bash
    aws secretsmanager put-secret-value \
      --secret-id ai-gateway/openai-api-key \
      --secret-string "sk-your-actual-key"

    aws secretsmanager put-secret-value \
      --secret-id ai-gateway/anthropic-api-key \
      --secret-string "sk-ant-your-actual-key"

    aws secretsmanager put-secret-value \
      --secret-id ai-gateway/google-api-key \
      --secret-string "your-google-api-key"

    aws secretsmanager put-secret-value \
      --secret-id ai-gateway/azure-api-key \
      --secret-string "your-azure-api-key"
    ```

:::caution
Secrets are initially created with the placeholder value `REPLACE_ME`. The gateway will fail to authenticate with providers until you replace these with real API keys.
:::


## Module Structure

The infrastructure is organized into 4 local modules under `infrastructure/modules/`:

```
infrastructure/
    main.tf              # Root module — wires modules together
    variables.tf         # Root-level input variables
    outputs.tf           # Root-level outputs
    versions.tf          # Terraform and provider version constraints
    providers.tf         # AWS provider configuration
    otel-config.yaml     # OpenTelemetry Collector configuration
    moved.tf             # State migration blocks (safe to remove after first apply)
    environments/
        dev.tfvars       # Dev environment variable overrides
        prod.tfvars      # Prod environment variable overrides
    modules/
        observability/   # KMS, log groups, dashboard, saved queries
        networking/      # VPC, ALB, WAF, VPC endpoints
        auth/            # Cognito user pool, resource server, JWT listener
        compute/         # ECS cluster/service, ECR, IAM, Secrets Manager
```

### Module Dependency Order

Modules must be applied in this order due to inter-module references:

1. **observability** -- Creates KMS keys and log groups needed by all other modules.
2. **networking** -- Creates VPC, ALB, and WAF. Receives the logs KMS key ARN from observability.
3. **auth** -- Creates Cognito resources and the JWT listener. Receives ALB ARN and target group from networking.
4. **compute** -- Creates ECS resources, ECR, IAM roles, and secrets. Receives subnets and ALB details from networking, and log group names from observability.

:::note
Terraform resolves this dependency graph automatically. You do not need to apply modules individually -- a single `terraform apply` handles the ordering.
:::


## Terraform Deployment

### Backend Configuration

The backend is configured as an empty `s3` block in `versions.tf`. You provide the actual bucket, key, region, and lock table at init time via `-backend-config` flags or a backend config file.

### Deploy with var-file (Direct Terraform)

```bash
cd infrastructure/

# Initialize with backend configuration
terraform init \
  -backend-config="bucket=ai-gateway-tfstate-dev" \
  -backend-config="key=terraform.tfstate" \
  -backend-config="region=us-east-1" \
  -backend-config="encrypt=true" \
  -backend-config="dynamodb_table=ai-gateway-tfstate-lock-dev"

# Preview changes
terraform plan -var-file=environments/dev.tfvars

# Apply
terraform apply -var-file=environments/dev.tfvars
```

For production:

```bash
terraform init \
  -backend-config="bucket=ai-gateway-tfstate-prod" \
  -backend-config="key=terraform.tfstate" \
  -backend-config="region=us-east-1" \
  -backend-config="encrypt=true" \
  -backend-config="dynamodb_table=ai-gateway-tfstate-lock-prod"

terraform plan -var-file=environments/prod.tfvars
terraform apply -var-file=environments/prod.tfvars
```

:::tip
Always run `terraform plan` before `terraform apply` and review the output carefully. Pay special attention to any resources being destroyed or replaced.
:::


### Deploy with Terragrunt (Recommended)

Terragrunt wraps Terraform to manage multiple environments with DRY configuration. See [Environments](environments.md) for the full Terragrunt directory layout.

```bash
# Deploy dev
cd terragrunt/dev/
terragrunt plan
terragrunt apply

# Deploy prod
cd terragrunt/prod/
terragrunt plan
terragrunt apply
```

Terragrunt automatically:

- Configures the S3 backend with environment-specific bucket and lock table names
- Generates the provider block with the correct region and tags
- Merges common inputs (from `_env/common.hcl`) with environment-specific inputs

## Updating the Gateway Image Version

The Portkey gateway image version is controlled by the `portkey_image` variable. To update:

1. **Update the variable** in the appropriate tfvars or Terragrunt inputs:

    ```hcl
    portkey_image = "portkeyai/gateway:1.16.0"
    ```

    Or for Terragrunt, update `_env/common.hcl`:

    ```hcl
    locals {
      project_name  = "ai-gateway"
      portkey_image = "portkeyai/gateway:1.16.0"
    }
    ```

2. **Apply the change**:

    ```bash
    terraform plan -var-file=environments/prod.tfvars
    terraform apply -var-file=environments/prod.tfvars
    ```

3. ECS performs a **rolling deployment** automatically (see below).

## Rolling Deployments

The ECS service is configured for zero-downtime rolling deployments:

| Setting | Value | Effect |
|---|---|---|
| `deployment_minimum_healthy_percent` | 100 | All existing tasks stay running during deployment |
| `deployment_maximum_percent` | 200 | New tasks are launched alongside existing ones |
| Circuit breaker | Enabled with rollback | Automatically rolls back if new tasks fail health checks |

When you update the task definition (via `terraform apply` or `aws ecs update-service --force-new-deployment`), ECS:

1. Launches new tasks with the updated definition
2. Waits for new tasks to pass ALB health checks (HTTP 200 on port 8787, path `/`)
3. Drains connections from old tasks (30-second deregistration delay)
4. Stops old tasks

:::note
The CI/CD pipeline (`ci.yml`) automates this for pushes to `main`: it pulls the Portkey image, re-tags it into ECR, then calls `aws ecs update-service --force-new-deployment` and waits for stability.
:::


### Manual Force Deployment

To trigger a redeployment without changing the task definition:

```bash
aws ecs update-service \
  --cluster ai-gateway-dev \
  --service ai-gateway-gateway \
  --force-new-deployment

# Wait for stability (timeout: 10 minutes)
aws ecs wait services-stable \
  --cluster ai-gateway-dev \
  --services ai-gateway-gateway
```

## Destroying Infrastructure

To tear down an environment:

```bash
# Direct Terraform
terraform destroy -var-file=environments/dev.tfvars

# Terragrunt
cd terragrunt/dev/
terragrunt destroy
```

:::danger
The Cognito User Pool has `deletion_protection = "ACTIVE"`. You must manually disable deletion protection before Terraform can destroy it:

```bash
aws cognito-idp update-user-pool \
  --user-pool-id <pool-id> \
  --deletion-protection INACTIVE
```
:::