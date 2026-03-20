# Environments

The AI Gateway supports two environments out of the box: **dev** and **prod**. Each environment gets its own Terraform state, its own set of AWS resources, and its own configuration tuned for its purpose.

## Dev vs Prod Comparison

| Setting | Dev | Prod |
|---|---|---|
| `environment` | `dev` | `prod` |
| `aws_region` | `us-east-1` | `us-east-1` |
| `gateway_cpu` | 512 (0.5 vCPU) | 1024 (1 vCPU) |
| `gateway_memory` | 1024 MiB | 2048 MiB |
| `gateway_desired_count` | 2 | 2 |
| `autoscaling_min_capacity` | 1 | 2 |
| `autoscaling_max_capacity` | 3 | 6 |
| `enable_waf` | `false` | `true` |
| `enable_jwt_auth` | `false` | `false` (enable when ready) |
| `certificate_arn` | `""` (HTTP only) | `""` (set to ACM cert ARN) |
| `cognito_domain_prefix` | `ai-gateway-dev` | `ai-gateway-prod` |
| Terraform state bucket | `ai-gateway-tfstate-dev` | `ai-gateway-tfstate-prod` |
| DynamoDB lock table | `ai-gateway-tfstate-lock-dev` | `ai-gateway-tfstate-lock-prod` |

!!! note
    Dev runs with WAF disabled and smaller task sizes to reduce cost. Prod enables WAF and allocates full resources for production traffic.

## tfvars Files

Environment-specific variables are stored in `infrastructure/environments/`:

### `dev.tfvars`

```hcl
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
enable_jwt_auth          = false
```

### `prod.tfvars`

```hcl
environment              = "prod"
aws_region               = "us-east-1"
gateway_desired_count    = 2
gateway_cpu              = 1024
gateway_memory           = 2048
autoscaling_min_capacity = 2
autoscaling_max_capacity = 6
enable_waf               = true
certificate_arn          = "" # Set to your ACM cert ARN
cognito_domain_prefix    = "ai-gateway-prod"
enable_jwt_auth          = false
```

## Terragrunt Directory Structure

Terragrunt provides a cleaner multi-environment workflow. The directory layout:

```
terragrunt/
    terragrunt.hcl          # Root config: remote state, provider generation
    _env/
        common.hcl          # Shared inputs (project_name, portkey_image)
    dev/
        env.hcl             # Dev-specific locals (environment, region)
        terragrunt.hcl      # Dev inputs (CPU, memory, WAF, scaling)
    prod/
        env.hcl             # Prod-specific locals (environment, region)
        terragrunt.hcl      # Prod inputs (CPU, memory, WAF, scaling)
```

### How It Works

The **root `terragrunt.hcl`** configures:

- **Remote state**: S3 bucket named `ai-gateway-tfstate-{environment}` with DynamoDB lock table `ai-gateway-tfstate-lock-{environment}`
- **Provider generation**: Injects the AWS provider block with the correct region and default tags (tagged `ManagedBy = "terragrunt"`)
- **Terraform source**: Points to `infrastructure/` at the repo root

Each **environment directory** contains:

- `env.hcl` -- defines `environment` and `aws_region` as locals
- `terragrunt.hcl` -- includes the root config, reads `common.hcl` and `env.hcl`, then merges all inputs

### Deploying with Terragrunt

```bash
# Dev
cd terragrunt/dev/
terragrunt init
terragrunt plan
terragrunt apply

# Prod
cd terragrunt/prod/
terragrunt init
terragrunt plan
terragrunt apply
```

## Creating Additional Environments

To add a new environment (e.g., `staging`):

### Option A: tfvars

1. Create `infrastructure/environments/staging.tfvars`:

    ```hcl
    environment              = "staging"
    aws_region               = "us-east-1"
    gateway_desired_count    = 2
    gateway_cpu              = 1024
    gateway_memory           = 2048
    autoscaling_min_capacity = 1
    autoscaling_max_capacity = 4
    enable_waf               = true
    certificate_arn          = "arn:aws:acm:us-east-1:123456789012:certificate/abc-123"
    cognito_domain_prefix    = "ai-gateway-staging"
    enable_jwt_auth          = false
    ```

2. Update the `environment` variable validation in `infrastructure/variables.tf` to allow `"staging"`:

    ```hcl
    validation {
      condition     = contains(["dev", "staging", "prod"], var.environment)
      error_message = "Environment must be 'dev', 'staging', or 'prod'."
    }
    ```

3. Create the state backend resources:

    ```bash
    aws s3api create-bucket --bucket ai-gateway-tfstate-staging --region us-east-1
    aws s3api put-bucket-versioning --bucket ai-gateway-tfstate-staging \
      --versioning-configuration Status=Enabled
    aws dynamodb create-table --table-name ai-gateway-tfstate-lock-staging \
      --attribute-definitions AttributeName=LockID,AttributeType=S \
      --key-schema AttributeName=LockID,KeyType=HASH \
      --billing-mode PAY_PER_REQUEST --region us-east-1
    ```

4. Deploy:

    ```bash
    cd infrastructure/
    terraform init \
      -backend-config="bucket=ai-gateway-tfstate-staging" \
      -backend-config="key=terraform.tfstate" \
      -backend-config="region=us-east-1" \
      -backend-config="encrypt=true" \
      -backend-config="dynamodb_table=ai-gateway-tfstate-lock-staging"

    terraform apply -var-file=environments/staging.tfvars
    ```

### Option B: Terragrunt

1. Create `terragrunt/staging/env.hcl`:

    ```hcl
    locals {
      environment = "staging"
      aws_region  = "us-east-1"
    }
    ```

2. Create `terragrunt/staging/terragrunt.hcl`:

    ```hcl
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
        autoscaling_min_capacity = 1
        autoscaling_max_capacity = 4
        enable_waf               = true
        certificate_arn          = "arn:aws:acm:us-east-1:123456789012:certificate/abc-123"
        cognito_domain_prefix    = "ai-gateway-staging"
        enable_jwt_auth          = false
      }
    )
    ```

3. Update the `environment` variable validation (same as Option A, step 2).

4. Deploy:

    ```bash
    cd terragrunt/staging/
    terragrunt apply
    ```

    Terragrunt automatically creates the S3 bucket and DynamoDB table if they do not exist.

## Common Customizations

### VPC CIDR

The default VPC CIDR is `10.0.0.0/16`. To avoid conflicts with existing VPCs, override it:

```hcl
vpc_cidr = "10.1.0.0/16"
```

Subnet allocation is derived automatically from the CIDR:

| Subnet Type | CIDR Derivation | Example (10.0.0.0/16) |
|---|---|---|
| Public Subnet AZ-a | `cidrsubnet(vpc_cidr, 8, 1)` | 10.0.1.0/24 |
| Public Subnet AZ-b | `cidrsubnet(vpc_cidr, 8, 2)` | 10.0.2.0/24 |
| Private Subnet AZ-a | `cidrsubnet(vpc_cidr, 8, 10)` | 10.0.10.0/24 |
| Private Subnet AZ-b | `cidrsubnet(vpc_cidr, 8, 20)` | 10.0.20.0/24 |

### Instance Sizing

CPU and memory are allocated at the ECS task level, then split between the gateway container and the ADOT sidecar:

| Variable | Task Total | Gateway | ADOT Sidecar |
|---|---|---|---|
| `gateway_cpu = 512` | 512 units | 256 units | 256 units |
| `gateway_cpu = 1024` | 1024 units | 768 units | 256 units |
| `gateway_memory = 1024` | 1024 MiB | 768 MiB | 256 MiB |
| `gateway_memory = 2048` | 2048 MiB | 1792 MiB | 256 MiB |

The ADOT sidecar always receives 256 CPU units and 256 MiB memory. The remainder goes to the Portkey gateway container.

### Autoscaling Thresholds

Two autoscaling policies are configured:

| Policy | Metric | Target | Scale-out Cooldown | Scale-in Cooldown |
|---|---|---|---|---|
| CPU | `ECSServiceAverageCPUUtilization` | 70% | 60s | 300s |
| Requests | `ALBRequestCountPerTarget` | 500 requests/target | 60s | 300s |

To adjust these, modify the `autoscaling_policies` block in `modules/compute/main.tf`.

### Region

Both dev and prod default to `us-east-1`. To deploy in a different region, update the `aws_region` in your tfvars or Terragrunt `env.hcl`. Ensure an ACM certificate is available in the target region.
