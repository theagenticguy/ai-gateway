# Admin Guide

## Audience

This guide is for **infrastructure engineers and platform team members** who deploy, operate, and maintain the AI Gateway. It assumes familiarity with AWS, Terraform, and container orchestration on ECS Fargate.

## What This Section Covers

| Guide | Description |
|---|---|
| [Deployment](deployment.md) | Terraform workflows, backend configuration, module structure, first-time setup |
| [Environments](environments.md) | Dev vs prod configuration, Terragrunt multi-environment setup, customizations |
| [Security](security.md) | WAFv2, JWT auth, Cognito, Secrets Manager, network isolation, CI security pipeline |
| [Monitoring](monitoring.md) | CloudWatch logs/dashboards, OTel collector, saved queries, key metrics |
| [Features](features.md) | B-series opt-in features: multi-client, fallback routing, cost attribution, guardrails, caching |

## Architecture Overview

The AI Gateway runs Portkey AI Gateway OSS (v1.15.2) on ECS Fargate behind an Application Load Balancer with Cognito M2M authentication and WAFv2 protection. All infrastructure is defined as Terraform with 4 local modules.

``` mermaid
flowchart TB
    subgraph clients["Clients"]
        C1["Service A"]
        C2["Service B"]
        C3["Service N"]
    end

    subgraph aws["AWS Account"]
        subgraph edge["Edge Layer"]
            WAF["WAFv2\n4 Managed Rules"]
            ALB["Application Load Balancer\nTLS 1.3 Termination"]
            COG["Cognito User Pool\nJWT Validation"]
        end

        subgraph vpc["VPC 10.0.0.0/16"]
            subgraph pub["Public Subnets (2 AZs)"]
                ALB
            end

            subgraph priv["Private Subnets (2 AZs)"]
                subgraph ecs["ECS Fargate Cluster"]
                    GW["Portkey Gateway\nPort 8787"]
                    OT["ADOT Sidecar\nOTel Collector"]
                end
            end

            NAT["NAT Gateway"]
            VPCE["VPC Endpoints\nECR, CW, SM, S3"]
        end

        subgraph obs["Observability"]
            CW["CloudWatch Logs\n365-day retention"]
            XR["X-Ray Traces"]
            EMF["CloudWatch Metrics\nEMF Namespace"]
            DASH["CloudWatch Dashboard"]
        end

        subgraph store["Secrets and Images"]
            ECR["ECR Repository\nKMS Encrypted"]
            SM["Secrets Manager\n4 Provider Keys"]
        end
    end

    subgraph providers["LLM Providers"]
        P1["Amazon Bedrock"]
        P2["OpenAI"]
        P3["Anthropic"]
        P4["Azure OpenAI"]
        P5["Google AI"]
    end

    C1 & C2 & C3 --> WAF
    WAF --> ALB
    ALB -->|"JWT check\n(optional)"| COG
    ALB -->|"Forward to\ntarget group"| GW
    GW --> OT
    OT --> CW & XR & EMF
    EMF --> DASH
    GW --> NAT
    NAT --> P1 & P2 & P3 & P4 & P5
    ecs -.-> VPCE
    VPCE -.-> ECR & SM & CW
```

## Module Dependency Graph

The root Terraform module wires together 4 local modules in a specific dependency order. The `observability` module must be created first because it provides the KMS key used by other modules for log encryption.

``` mermaid
flowchart LR
    OBS["observability\nKMS, Log Groups,\nDashboard, Queries"]
    NET["networking\nVPC, ALB, WAF,\nVPC Endpoints"]
    AUTH["auth\nCognito, JWT\nListener"]
    COMP["compute\nECS, ECR, IAM,\nSecrets Manager"]

    OBS -->|"logs_kms_key_arn"| NET
    NET -->|"alb_arn,\ntarget_group_arn"| AUTH
    NET -->|"subnets, SG,\ntarget_group"| COMP
    OBS -->|"log_group_names"| COMP
```

## Key Design Decisions

- **Single NAT Gateway** -- cost-optimized for non-critical workloads; upgrade to per-AZ NAT for production HA requirements.
- **Portkey OSS as upstream image** -- pulled from Docker Hub and re-tagged into ECR; no custom Dockerfile required.
- **ADOT sidecar** -- the AWS Distro for OpenTelemetry collector runs as a sidecar container in each ECS task, exporting traces to X-Ray and metrics via EMF.
- **S3 + DynamoDB backend** -- Terraform state is stored in S3 with DynamoDB locking, one state file per environment.
- **ALB JWT validation** -- native ALB action (no Lambda authorizer) validates Cognito-issued JWTs, requiring AWS provider v6.22+.
