# AI Gateway Infrastructure

Terraform root module for the AI Gateway — deploys VPC, ALB, ECS Fargate, Cognito M2M auth, WAF, and CloudWatch observability on AWS.

## Architecture

This module is composed of 4 local sub-modules:

| Module | Purpose |
|--------|---------|
| `modules/networking` | VPC, ALB, WAF, VPC endpoints |
| `modules/auth` | Cognito User Pool, M2M client, ALB JWT validation |
| `modules/compute` | ECS Fargate, ECR, IAM roles, Secrets Manager |
| `modules/observability` | CloudWatch log groups, KMS encryption, dashboard |

<!-- BEGIN_TF_DOCS -->


## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.9 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | ~> 6.22 |

## Providers

| Name | Version |
|------|---------|
| <a name="provider_aws"></a> [aws](#provider\_aws) | 6.37.0 |

## Modules

| Name | Source | Version |
|------|--------|---------|
| <a name="module_auth"></a> [auth](#module\_auth) | ./modules/auth | n/a |
| <a name="module_cache"></a> [cache](#module\_cache) | ./modules/cache | n/a |
| <a name="module_clients"></a> [clients](#module\_clients) | ./modules/clients | n/a |
| <a name="module_compute"></a> [compute](#module\_compute) | ./modules/compute | n/a |
| <a name="module_cost_attribution"></a> [cost\_attribution](#module\_cost\_attribution) | ./modules/cost_attribution | n/a |
| <a name="module_guardrails"></a> [guardrails](#module\_guardrails) | ./modules/guardrails | n/a |
| <a name="module_networking"></a> [networking](#module\_networking) | ./modules/networking | n/a |
| <a name="module_observability"></a> [observability](#module\_observability) | ./modules/observability | n/a |

## Resources

| Name | Type |
|------|------|

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_environment"></a> [environment](#input\_environment) | Deployment environment (dev or prod) | `string` | n/a | yes |
| <a name="input_autoscaling_max_capacity"></a> [autoscaling\_max\_capacity](#input\_autoscaling\_max\_capacity) | Maximum number of ECS tasks for autoscaling | `number` | `6` | no |
| <a name="input_autoscaling_min_capacity"></a> [autoscaling\_min\_capacity](#input\_autoscaling\_min\_capacity) | Minimum number of ECS tasks for autoscaling | `number` | `2` | no |
| <a name="input_aws_region"></a> [aws\_region](#input\_aws\_region) | AWS region to deploy into | `string` | `"us-east-1"` | no |
| <a name="input_cache_node_type"></a> [cache\_node\_type](#input\_cache\_node\_type) | ElastiCache node instance type | `string` | `"cache.t4g.micro"` | no |
| <a name="input_certificate_arn"></a> [certificate\_arn](#input\_certificate\_arn) | ACM certificate ARN for HTTPS listener | `string` | `""` | no |
| <a name="input_client_configs"></a> [client\_configs](#input\_client\_configs) | Map of team configurations for per-team Cognito app clients.<br/>Each key is the team identifier; value specifies allowed OAuth scopes<br/>and a human-readable description.<br/><br/>Example:<br/>  client\_configs = {<br/>    platform = {<br/>      allowed\_scopes = ["https://gateway.internal/invoke"]<br/>      description    = "Platform engineering team"<br/>    }<br/>    ml-ops = {<br/>      allowed\_scopes = ["https://gateway.internal/invoke", "https://gateway.internal/admin"]<br/>      description    = "ML Operations team"<br/>    }<br/>  } | <pre>map(object({<br/>    allowed_scopes = list(string)<br/>    description    = string<br/>  }))</pre> | `{}` | no |
| <a name="input_cognito_domain_prefix"></a> [cognito\_domain\_prefix](#input\_cognito\_domain\_prefix) | Cognito User Pool domain prefix for the token endpoint. Leave empty to skip domain creation. | `string` | `""` | no |
| <a name="input_cognito_user_pool_id"></a> [cognito\_user\_pool\_id](#input\_cognito\_user\_pool\_id) | Cognito User Pool ID for JWT validation. Leave empty to disable JWT auth. | `string` | `""` | no |
| <a name="input_enable_cache"></a> [enable\_cache](#input\_enable\_cache) | Whether to deploy an ElastiCache Redis cluster for response caching | `bool` | `false` | no |
| <a name="input_enable_cost_attribution"></a> [enable\_cost\_attribution](#input\_enable\_cost\_attribution) | Whether to deploy the cost attribution Lambda pipeline | `bool` | `false` | no |
| <a name="input_enable_guardrails"></a> [enable\_guardrails](#input\_enable\_guardrails) | Whether to enable Bedrock Guardrails for content safety filtering | `bool` | `false` | no |
| <a name="input_enable_jwt_auth"></a> [enable\_jwt\_auth](#input\_enable\_jwt\_auth) | Whether to enable ALB JWT validation. Requires certificate\_arn and cognito\_user\_pool\_id. | `bool` | `false` | no |
| <a name="input_enable_provider_fallback"></a> [enable\_provider\_fallback](#input\_enable\_provider\_fallback) | Whether to enable provider fallback routing. When true, routing configs are injected into the gateway container as environment variables. | `bool` | `false` | no |
| <a name="input_enable_waf"></a> [enable\_waf](#input\_enable\_waf) | Whether to enable WAF on the ALB | `bool` | `true` | no |
| <a name="input_gateway_cpu"></a> [gateway\_cpu](#input\_gateway\_cpu) | Total CPU units for the gateway ECS task | `number` | `1024` | no |
| <a name="input_gateway_desired_count"></a> [gateway\_desired\_count](#input\_gateway\_desired\_count) | Desired number of gateway ECS tasks | `number` | `2` | no |
| <a name="input_gateway_memory"></a> [gateway\_memory](#input\_gateway\_memory) | Total memory (MiB) for the gateway ECS task | `number` | `2048` | no |
| <a name="input_guardrails_blocked_topics"></a> [guardrails\_blocked\_topics](#input\_guardrails\_blocked\_topics) | List of topics to block, each with a name and definition | <pre>list(object({<br/>    name       = string<br/>    definition = string<br/>    examples   = optional(list(string), [])<br/>  }))</pre> | <pre>[<br/>  {<br/>    "definition": "Discussions or recommendations about competitor products and services.",<br/>    "examples": [<br/>      "Tell me about competing AI platforms"<br/>    ],<br/>    "name": "competitor_products"<br/>  },<br/>  {<br/>    "definition": "Internal financial data, revenue figures, or unreleased business metrics.",<br/>    "examples": [<br/>      "What is the company revenue this quarter"<br/>    ],<br/>    "name": "internal_financials"<br/>  }<br/>]</pre> | no |
| <a name="input_guardrails_blocked_words"></a> [guardrails\_blocked\_words](#input\_guardrails\_blocked\_words) | List of words or phrases to block in inputs and outputs | `list(string)` | `[]` | no |
| <a name="input_guardrails_content_filter_strength"></a> [guardrails\_content\_filter\_strength](#input\_guardrails\_content\_filter\_strength) | Strength of content filters (LOW, MEDIUM, HIGH) | `string` | `"HIGH"` | no |
| <a name="input_portkey_image"></a> [portkey\_image](#input\_portkey\_image) | Docker image for the Portkey AI Gateway | `string` | `"portkeyai/gateway:1.15.2"` | no |
| <a name="input_project_name"></a> [project\_name](#input\_project\_name) | Project name used for resource naming | `string` | `"ai-gateway"` | no |
| <a name="input_routing_configs"></a> [routing\_configs](#input\_routing\_configs) | Map of named routing configurations as JSON strings. Keys are config names (e.g. 'anthropic', 'openai'), values are Portkey-compatible routing JSON. | `map(string)` | `{}` | no |
| <a name="input_vpc_cidr"></a> [vpc\_cidr](#input\_vpc\_cidr) | CIDR block for the VPC | `string` | `"10.0.0.0/16"` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_alb_dns_name"></a> [alb\_dns\_name](#output\_alb\_dns\_name) | DNS name of the Application Load Balancer |
| <a name="output_cognito_client_id"></a> [cognito\_client\_id](#output\_cognito\_client\_id) | Cognito M2M client ID |
| <a name="output_cognito_token_endpoint"></a> [cognito\_token\_endpoint](#output\_cognito\_token\_endpoint) | Cognito token endpoint URL |
| <a name="output_cognito_user_pool_arn"></a> [cognito\_user\_pool\_arn](#output\_cognito\_user\_pool\_arn) | Cognito User Pool ARN |
| <a name="output_cognito_user_pool_id"></a> [cognito\_user\_pool\_id](#output\_cognito\_user\_pool\_id) | Cognito User Pool ID |
| <a name="output_ecr_repository_url"></a> [ecr\_repository\_url](#output\_ecr\_repository\_url) | URL of the ECR repository |
| <a name="output_ecs_cluster_name"></a> [ecs\_cluster\_name](#output\_ecs\_cluster\_name) | Name of the ECS cluster |
| <a name="output_ecs_service_name"></a> [ecs\_service\_name](#output\_ecs\_service\_name) | Name of the ECS service |
| <a name="output_guardrail_arn"></a> [guardrail\_arn](#output\_guardrail\_arn) | Bedrock Guardrail ARN |
| <a name="output_guardrail_id"></a> [guardrail\_id](#output\_guardrail\_id) | Bedrock Guardrail ID |
| <a name="output_team_client_ids"></a> [team\_client\_ids](#output\_team\_client\_ids) | Map of team name to Cognito app client ID (empty if no client\_configs) |
| <a name="output_team_client_secrets"></a> [team\_client\_secrets](#output\_team\_client\_secrets) | Map of team name to Cognito app client secret (empty if no client\_configs) |
| <a name="output_vpc_id"></a> [vpc\_id](#output\_vpc\_id) | ID of the VPC |
<!-- END_TF_DOCS -->

## Environments

| Environment | File | Notes |
|-------------|------|-------|
| dev | `environments/dev.tfvars` | Lower resources, WAF disabled |
| prod | `environments/prod.tfvars` | Full resources, WAF enabled |

## Usage

```bash
# With Terragrunt (recommended)
cd terragrunt/dev && terragrunt plan

# Standalone Terraform
cd infrastructure
terraform init -backend-config=environments/dev.tfvars
terraform plan -var-file=environments/dev.tfvars
```
