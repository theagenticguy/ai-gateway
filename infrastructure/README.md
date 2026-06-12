# AI Gateway Infrastructure

Terraform root module for the AI Gateway — deploys VPC, ALB, ECS Fargate, Cognito M2M auth, WAF, and CloudWatch observability on AWS.

## Architecture

This root module composes 17 local sub-modules. The core data plane is always deployed; the remaining modules are feature-gated by input variables (see the Inputs table below).

**Core (always deployed):**

| Module | Purpose |
|--------|---------|
| `modules/networking` | VPC, ALB, WAF, VPC endpoints |
| `modules/auth` | Cognito User Pool, M2M client, ALB JWT validation |
| `modules/compute` | ECS Fargate, ECR, IAM roles, Secrets Manager |
| `modules/observability` | KMS-encrypted CloudWatch log groups and dashboard |
| `modules/cache` | ElastiCache Redis for response caching |
| `modules/guardrails` | Bedrock Guardrails for content safety filtering |
| `modules/content_scanner` | Lambda + Function URL for PII redaction and injection detection |
| `modules/appconfig` | Feature flags and dynamic config for the content scanner |
| `modules/cost_attribution` | Lambda pipeline attributing spend per team via CloudWatch metrics |
| `modules/inspector` | Continuous ECR vulnerability scanning |

**Optional (feature-gated):**

| Module | Purpose | Enabled by |
|--------|---------|-----------|
| `modules/clients` | Per-team Cognito app clients for multi-tenant M2M access | `client_configs` non-empty |
| `modules/admin_api` | API Gateway REST API admin plane with Cognito authorizer | `enable_admin_api` |
| `modules/routing` | DynamoDB table for custom routing configs | `enable_admin_api` |
| `modules/team_registration` | Self-service API for team onboarding | `enable_admin_api` |
| `modules/budgets` | DynamoDB tables for budget definitions and usage tracking | `enable_budgets` |
| `modules/chargeback` | Monthly chargeback report pipeline | `enable_chargeback` (+ `enable_budgets`) |
| `modules/audit_log` | Kinesis Firehose → S3 (Parquet) with Glue Catalog | `enable_audit_log` |

<!-- BEGIN_TF_DOCS -->


## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | ~> 1.14 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | ~> 6.22 |

## Providers

| Name | Version |
|------|---------|
| <a name="provider_aws"></a> [aws](#provider\_aws) | 6.50.0 |
| <a name="provider_terraform"></a> [terraform](#provider\_terraform) | n/a |

## Modules

| Name | Source | Version |
|------|--------|---------|
| <a name="module_admin_api"></a> [admin\_api](#module\_admin\_api) | ./modules/admin_api | n/a |
| <a name="module_appconfig"></a> [appconfig](#module\_appconfig) | ./modules/appconfig | n/a |
| <a name="module_audit_log"></a> [audit\_log](#module\_audit\_log) | ./modules/audit_log | n/a |
| <a name="module_auth"></a> [auth](#module\_auth) | ./modules/auth | n/a |
| <a name="module_budgets"></a> [budgets](#module\_budgets) | ./modules/budgets | n/a |
| <a name="module_cache"></a> [cache](#module\_cache) | ./modules/cache | n/a |
| <a name="module_chargeback"></a> [chargeback](#module\_chargeback) | ./modules/chargeback | n/a |
| <a name="module_clients"></a> [clients](#module\_clients) | ./modules/clients | n/a |
| <a name="module_compute"></a> [compute](#module\_compute) | ./modules/compute | n/a |
| <a name="module_content_scanner"></a> [content\_scanner](#module\_content\_scanner) | ./modules/content_scanner | n/a |
| <a name="module_cost_attribution"></a> [cost\_attribution](#module\_cost\_attribution) | ./modules/cost_attribution | n/a |
| <a name="module_guardrails"></a> [guardrails](#module\_guardrails) | ./modules/guardrails | n/a |
| <a name="module_inspector"></a> [inspector](#module\_inspector) | ./modules/inspector | n/a |
| <a name="module_networking"></a> [networking](#module\_networking) | ./modules/networking | n/a |
| <a name="module_observability"></a> [observability](#module\_observability) | ./modules/observability | n/a |
| <a name="module_routing"></a> [routing](#module\_routing) | ./modules/routing | n/a |
| <a name="module_team_registration"></a> [team\_registration](#module\_team\_registration) | ./modules/team_registration | n/a |

## Resources

| Name | Type |
|------|------|
| [terraform_data.jwt_auth_guard](https://registry.terraform.io/providers/hashicorp/terraform/latest/docs/resources/data) | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_environment"></a> [environment](#input\_environment) | Deployment environment (dev or prod) | `string` | n/a | yes |
| <a name="input_alarm_sns_topic_arns"></a> [alarm\_sns\_topic\_arns](#input\_alarm\_sns\_topic\_arns) | List of SNS topic ARNs for CloudWatch alarm notifications. If empty, a default topic is created. | `list(string)` | `[]` | no |
| <a name="input_autoscaling_max_capacity"></a> [autoscaling\_max\_capacity](#input\_autoscaling\_max\_capacity) | Maximum number of ECS tasks for autoscaling | `number` | `6` | no |
| <a name="input_autoscaling_min_capacity"></a> [autoscaling\_min\_capacity](#input\_autoscaling\_min\_capacity) | Minimum number of ECS tasks for autoscaling | `number` | `2` | no |
| <a name="input_aws_region"></a> [aws\_region](#input\_aws\_region) | AWS region to deploy into | `string` | `"us-east-1"` | no |
| <a name="input_budget_alarm_threshold_pct"></a> [budget\_alarm\_threshold\_pct](#input\_budget\_alarm\_threshold\_pct) | Percentage of daily budget that triggers the budget utilization alarm | `number` | `80` | no |
| <a name="input_budget_limit_daily_usd"></a> [budget\_limit\_daily\_usd](#input\_budget\_limit\_daily\_usd) | Daily budget limit in USD for dashboard gauge and budget alarm | `number` | `1000` | no |
| <a name="input_cache_node_type"></a> [cache\_node\_type](#input\_cache\_node\_type) | ElastiCache node instance type | `string` | `"cache.t4g.micro"` | no |
| <a name="input_callback_urls"></a> [callback\_urls](#input\_callback\_urls) | List of allowed callback URLs for the user SSO client | `list(string)` | <pre>[<br/>  "http://localhost:3000/callback"<br/>]</pre> | no |
| <a name="input_certificate_arn"></a> [certificate\_arn](#input\_certificate\_arn) | ACM certificate ARN for HTTPS listener | `string` | `""` | no |
| <a name="input_client_configs"></a> [client\_configs](#input\_client\_configs) | Map of team configurations for per-team Cognito app clients.<br/>Each key is the team identifier; value specifies allowed OAuth scopes<br/>and a human-readable description.<br/><br/>Example:<br/>  client\_configs = {<br/>    platform = {<br/>      allowed\_scopes = ["https://gateway.internal/invoke"]<br/>      description    = "Platform engineering team"<br/>    }<br/>    ml-ops = {<br/>      allowed\_scopes = ["https://gateway.internal/invoke", "https://gateway.internal/admin"]<br/>      description    = "ML Operations team"<br/>    }<br/>  } | <pre>map(object({<br/>    allowed_scopes = list(string)<br/>    description    = string<br/>  }))</pre> | `{}` | no |
| <a name="input_cognito_domain_prefix"></a> [cognito\_domain\_prefix](#input\_cognito\_domain\_prefix) | Cognito User Pool domain prefix for the token endpoint. Leave empty to skip domain creation. | `string` | `""` | no |
| <a name="input_cognito_user_pool_id"></a> [cognito\_user\_pool\_id](#input\_cognito\_user\_pool\_id) | Cognito User Pool ID for JWT validation. Leave empty to disable JWT auth. | `string` | `""` | no |
| <a name="input_content_scanner_default_injection_mode"></a> [content\_scanner\_default\_injection\_mode](#input\_content\_scanner\_default\_injection\_mode) | Default injection scan mode when team config is missing (off, detect, redact, block) | `string` | `"detect"` | no |
| <a name="input_content_scanner_default_pii_mode"></a> [content\_scanner\_default\_pii\_mode](#input\_content\_scanner\_default\_pii\_mode) | Default PII scan mode when team config is missing (off, detect, redact, block) | `string` | `"detect"` | no |
| <a name="input_enable_admin_api"></a> [enable\_admin\_api](#input\_enable\_admin\_api) | Enable the API Gateway admin plane (also enables team\_registration and routing modules) | `bool` | `false` | no |
| <a name="input_enable_appconfig"></a> [enable\_appconfig](#input\_enable\_appconfig) | Enable AWS AppConfig for feature flag management (scanner toggle) | `bool` | `false` | no |
| <a name="input_enable_audit_log"></a> [enable\_audit\_log](#input\_enable\_audit\_log) | Enable audit logging via Firehose to S3 | `bool` | `false` | no |
| <a name="input_enable_budgets"></a> [enable\_budgets](#input\_enable\_budgets) | Whether to deploy the budget and usage tracking DynamoDB tables | `bool` | `false` | no |
| <a name="input_enable_cache"></a> [enable\_cache](#input\_enable\_cache) | Whether to deploy an ElastiCache Redis cluster for response caching | `bool` | `false` | no |
| <a name="input_enable_chargeback"></a> [enable\_chargeback](#input\_enable\_chargeback) | Whether to deploy the monthly chargeback report pipeline (requires enable\_budgets) | `bool` | `false` | no |
| <a name="input_enable_content_scanner"></a> [enable\_content\_scanner](#input\_enable\_content\_scanner) | Whether to deploy the content scanner Lambda (PII redaction + injection detection) | `bool` | `false` | no |
| <a name="input_enable_cost_attribution"></a> [enable\_cost\_attribution](#input\_enable\_cost\_attribution) | Whether to deploy the cost attribution Lambda pipeline | `bool` | `false` | no |
| <a name="input_enable_guardrails"></a> [enable\_guardrails](#input\_enable\_guardrails) | Whether to enable Bedrock Guardrails for content safety filtering | `bool` | `false` | no |
| <a name="input_enable_inspector"></a> [enable\_inspector](#input\_enable\_inspector) | Whether to enable Amazon Inspector enhanced scanning for ECR repositories | `bool` | `false` | no |
| <a name="input_enable_jwt_auth"></a> [enable\_jwt\_auth](#input\_enable\_jwt\_auth) | Whether to enable ALB JWT validation. Secure default (true) for this reference architecture — requires certificate\_arn and cognito\_user\_pool\_id (a precondition in guards.tf fails the plan if either is empty). Set to false only for a deliberately unauthenticated deployment (e.g. a no-cert local smoke test). | `bool` | `true` | no |
| <a name="input_enable_provider_fallback"></a> [enable\_provider\_fallback](#input\_enable\_provider\_fallback) | Whether to enable provider fallback routing. When true, routing configs are injected into the gateway container as environment variables. | `bool` | `false` | no |
| <a name="input_enable_user_auth"></a> [enable\_user\_auth](#input\_enable\_user\_auth) | Whether to enable user-facing SSO authentication (authorization\_code flow) | `bool` | `false` | no |
| <a name="input_enable_waf"></a> [enable\_waf](#input\_enable\_waf) | Whether to enable WAF on the ALB | `bool` | `true` | no |
| <a name="input_error_rate_evaluation_minutes"></a> [error\_rate\_evaluation\_minutes](#input\_error\_rate\_evaluation\_minutes) | Number of 1-minute evaluation periods for the error rate alarm | `number` | `5` | no |
| <a name="input_error_rate_threshold_pct"></a> [error\_rate\_threshold\_pct](#input\_error\_rate\_threshold\_pct) | Error rate percentage threshold that triggers the high error rate alarm | `number` | `5` | no |
| <a name="input_gateway_cpu"></a> [gateway\_cpu](#input\_gateway\_cpu) | Total CPU units for the gateway ECS task | `number` | `1024` | no |
| <a name="input_gateway_desired_count"></a> [gateway\_desired\_count](#input\_gateway\_desired\_count) | Desired number of gateway ECS tasks | `number` | `2` | no |
| <a name="input_gateway_memory"></a> [gateway\_memory](#input\_gateway\_memory) | Total memory (MiB) for the gateway ECS task | `number` | `2048` | no |
| <a name="input_group_mapping"></a> [group\_mapping](#input\_group\_mapping) | Mapping from IdP group names to gateway claims (team, org\_unit, cost\_center, tenant\_tier) | <pre>map(object({<br/>    team        = string<br/>    org_unit    = string<br/>    cost_center = string<br/>    tenant_tier = string<br/>  }))</pre> | `{}` | no |
| <a name="input_guardrails_blocked_topics"></a> [guardrails\_blocked\_topics](#input\_guardrails\_blocked\_topics) | List of topics to block, each with a name and definition | <pre>list(object({<br/>    name       = string<br/>    definition = string<br/>    examples   = optional(list(string), [])<br/>  }))</pre> | <pre>[<br/>  {<br/>    "definition": "Discussions or recommendations about competitor products and services.",<br/>    "examples": [<br/>      "Tell me about competing AI platforms"<br/>    ],<br/>    "name": "competitor_products"<br/>  },<br/>  {<br/>    "definition": "Internal financial data, revenue figures, or unreleased business metrics.",<br/>    "examples": [<br/>      "What is the company revenue this quarter"<br/>    ],<br/>    "name": "internal_financials"<br/>  }<br/>]</pre> | no |
| <a name="input_guardrails_blocked_words"></a> [guardrails\_blocked\_words](#input\_guardrails\_blocked\_words) | List of words or phrases to block in inputs and outputs | `list(string)` | `[]` | no |
| <a name="input_guardrails_content_filter_strength"></a> [guardrails\_content\_filter\_strength](#input\_guardrails\_content\_filter\_strength) | Strength of content filters (LOW, MEDIUM, HIGH) | `string` | `"HIGH"` | no |
| <a name="input_identity_providers"></a> [identity\_providers](#input\_identity\_providers) | Map of external identity providers (SAML/OIDC) to federate with Cognito | <pre>map(object({<br/>    provider_type     = string<br/>    metadata_url      = string<br/>    provider_details  = map(string)<br/>    attribute_mapping = map(string)<br/>  }))</pre> | `{}` | no |
| <a name="input_latency_evaluation_minutes"></a> [latency\_evaluation\_minutes](#input\_latency\_evaluation\_minutes) | Number of 1-minute evaluation periods for the latency alarm | `number` | `5` | no |
| <a name="input_logout_urls"></a> [logout\_urls](#input\_logout\_urls) | List of allowed logout URLs for the user SSO client | `list(string)` | <pre>[<br/>  "http://localhost:3000/logout"<br/>]</pre> | no |
| <a name="input_p99_latency_threshold_ms"></a> [p99\_latency\_threshold\_ms](#input\_p99\_latency\_threshold\_ms) | P99 latency threshold in milliseconds that triggers the high latency alarm | `number` | `30000` | no |
| <a name="input_portkey_image"></a> [portkey\_image](#input\_portkey\_image) | Docker image URI for the AI Gateway (custom-built from Portkey OSS, pushed to ECR + GHCR by release workflow) | `string` | `"ghcr.io/theagenticguy/ai-gateway:latest"` | no |
| <a name="input_project_name"></a> [project\_name](#input\_project\_name) | Project name used for resource naming | `string` | `"ai-gateway"` | no |
| <a name="input_provider_down_minutes"></a> [provider\_down\_minutes](#input\_provider\_down\_minutes) | Number of consecutive 1-minute periods with zero requests before declaring a provider down | `number` | `10` | no |
| <a name="input_routing_configs"></a> [routing\_configs](#input\_routing\_configs) | Map of named routing configurations as JSON strings. Keys are config names (e.g. 'anthropic', 'openai'), values are Portkey-compatible routing JSON. | `map(string)` | `{}` | no |
| <a name="input_vpc_cidr"></a> [vpc\_cidr](#input\_vpc\_cidr) | CIDR block for the VPC | `string` | `"10.0.0.0/16"` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_admin_api_execution_arn"></a> [admin\_api\_execution\_arn](#output\_admin\_api\_execution\_arn) | Admin API Gateway execution ARN (for Lambda permissions) |
| <a name="output_admin_api_url"></a> [admin\_api\_url](#output\_admin\_api\_url) | Admin API Gateway invoke URL |
| <a name="output_alb_dns_name"></a> [alb\_dns\_name](#output\_alb\_dns\_name) | DNS name of the Application Load Balancer |
| <a name="output_audit_log_bucket"></a> [audit\_log\_bucket](#output\_audit\_log\_bucket) | Audit log S3 bucket name |
| <a name="output_audit_log_firehose_stream"></a> [audit\_log\_firehose\_stream](#output\_audit\_log\_firehose\_stream) | Audit log Firehose delivery stream name |
| <a name="output_audit_log_glue_database"></a> [audit\_log\_glue\_database](#output\_audit\_log\_glue\_database) | Glue catalog database for audit log queries |
| <a name="output_budgets_kms_key_arn"></a> [budgets\_kms\_key\_arn](#output\_budgets\_kms\_key\_arn) | ARN of the KMS key used for budget table encryption |
| <a name="output_budgets_lambda_policy_arn"></a> [budgets\_lambda\_policy\_arn](#output\_budgets\_lambda\_policy\_arn) | ARN of the IAM policy for Lambda access to budget tables |
| <a name="output_budgets_table_arn"></a> [budgets\_table\_arn](#output\_budgets\_table\_arn) | ARN of the budgets DynamoDB table |
| <a name="output_budgets_table_name"></a> [budgets\_table\_name](#output\_budgets\_table\_name) | Name of the budgets DynamoDB table |
| <a name="output_chargeback_lambda_arn"></a> [chargeback\_lambda\_arn](#output\_chargeback\_lambda\_arn) | ARN of the chargeback report Lambda function |
| <a name="output_chargeback_report_bucket"></a> [chargeback\_report\_bucket](#output\_chargeback\_report\_bucket) | Name of the S3 bucket storing chargeback reports |
| <a name="output_chargeback_state_machine_arn"></a> [chargeback\_state\_machine\_arn](#output\_chargeback\_state\_machine\_arn) | ARN of the chargeback Step Functions state machine |
| <a name="output_cognito_client_id"></a> [cognito\_client\_id](#output\_cognito\_client\_id) | Cognito M2M client ID |
| <a name="output_cognito_token_endpoint"></a> [cognito\_token\_endpoint](#output\_cognito\_token\_endpoint) | Cognito token endpoint URL |
| <a name="output_cognito_user_pool_arn"></a> [cognito\_user\_pool\_arn](#output\_cognito\_user\_pool\_arn) | Cognito User Pool ARN |
| <a name="output_cognito_user_pool_id"></a> [cognito\_user\_pool\_id](#output\_cognito\_user\_pool\_id) | Cognito User Pool ID |
| <a name="output_content_scanner_function_arn"></a> [content\_scanner\_function\_arn](#output\_content\_scanner\_function\_arn) | ARN of the content scanner Lambda function |
| <a name="output_content_scanner_function_url"></a> [content\_scanner\_function\_url](#output\_content\_scanner\_function\_url) | Lambda Function URL for the content scanner |
| <a name="output_ecr_repository_url"></a> [ecr\_repository\_url](#output\_ecr\_repository\_url) | URL of the ECR repository |
| <a name="output_ecs_cluster_name"></a> [ecs\_cluster\_name](#output\_ecs\_cluster\_name) | Name of the ECS cluster |
| <a name="output_ecs_service_name"></a> [ecs\_service\_name](#output\_ecs\_service\_name) | Name of the ECS service |
| <a name="output_guardrail_arn"></a> [guardrail\_arn](#output\_guardrail\_arn) | Bedrock Guardrail ARN |
| <a name="output_guardrail_id"></a> [guardrail\_id](#output\_guardrail\_id) | Bedrock Guardrail ID |
| <a name="output_hosted_ui_url"></a> [hosted\_ui\_url](#output\_hosted\_ui\_url) | Cognito Hosted UI URL for SSO login (empty if user auth is disabled) |
| <a name="output_routing_config_function_url"></a> [routing\_config\_function\_url](#output\_routing\_config\_function\_url) | Lambda Function URL for routing config management |
| <a name="output_routing_configs_table_name"></a> [routing\_configs\_table\_name](#output\_routing\_configs\_table\_name) | Name of the routing configs DynamoDB table |
| <a name="output_team_client_ids"></a> [team\_client\_ids](#output\_team\_client\_ids) | Map of team name to Cognito app client ID (empty if no client\_configs) |
| <a name="output_team_client_secrets"></a> [team\_client\_secrets](#output\_team\_client\_secrets) | Map of team name to Cognito app client secret (empty if no client\_configs) |
| <a name="output_team_registration_function_url"></a> [team\_registration\_function\_url](#output\_team\_registration\_function\_url) | Lambda Function URL for team registration |
| <a name="output_teams_table_name"></a> [teams\_table\_name](#output\_teams\_table\_name) | Name of the teams DynamoDB table |
| <a name="output_usage_table_arn"></a> [usage\_table\_arn](#output\_usage\_table\_arn) | ARN of the usage DynamoDB table |
| <a name="output_usage_table_name"></a> [usage\_table\_name](#output\_usage\_table\_name) | Name of the usage DynamoDB table |
| <a name="output_user_client_id"></a> [user\_client\_id](#output\_user\_client\_id) | Cognito User SSO client ID (empty if user auth is disabled) |
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
