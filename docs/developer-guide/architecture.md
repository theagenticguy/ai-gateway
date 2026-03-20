# Architecture

This page provides a complete mental model of the AI Gateway system: how requests flow from client agents through the infrastructure, how modules are organized, and why key design decisions were made.

## High-Level System Architecture

The gateway sits between AI coding agents and LLM model providers, handling authentication, routing, and observability.

``` mermaid
flowchart LR
    subgraph Clients
        A1[Claude Code]
        A2[OpenCode]
        A3[Goose / Continue /<br>LangChain / Codex]
    end

    subgraph AWS Cloud
        subgraph Public Subnets
            WAF[WAF v2<br>Rate Limiting +<br>AWS Managed Rules]
            ALB[ALB<br>TLS 1.3 +<br>JWT Validation]
        end

        subgraph Private Subnets
            GW[Portkey Gateway<br>Port 8787]
            OTEL[OTel Sidecar<br>Collector]
        end

        COG[Cognito<br>M2M Token Issuer]
        ECR[ECR<br>Container Registry]
        SM[Secrets Manager<br>Provider API Keys]
        CW[CloudWatch<br>Logs + Metrics]
        XRAY[X-Ray<br>Traces]
        KMS[KMS<br>Log Encryption]
    end

    subgraph Providers
        BED[AWS Bedrock]
        OAI[OpenAI]
        ANT[Anthropic]
        GOO[Google Vertex AI]
        AZR[Azure OpenAI]
    end

    A1 -->|/v1/messages| ALB
    A2 -->|/v1/chat/completions| ALB
    A3 -->|/v1/chat/completions| ALB

    WAF --- ALB
    ALB -->|JWT valid| GW
    COG -.->|JWKS| ALB
    ECR -.->|Image pull| GW
    SM -.->|API keys| GW
    GW --- OTEL
    OTEL --> CW
    OTEL --> XRAY
    KMS -.->|Encryption| CW

    GW --> BED
    GW --> OAI
    GW --> ANT
    GW --> GOO
    GW --> AZR
```

## Design Principles

**Lightweight** -- The gateway adds minimal overhead. Portkey OSS is a ~62 MB container that proxies requests with sub-millisecond added latency. No database, no state, no complex middleware.

**Zero per-request auth cost** -- ALB-native JWT validation means authentication adds no cost and no extra latency beyond the ALB itself. No API Gateway, no Lambda authorizer, no per-request charges. See [ADR-005](adr-index.md).

**Multi-provider** -- A single gateway instance routes to Bedrock, OpenAI, Anthropic, Google Vertex AI, and Azure OpenAI through Portkey's 200+ model provider support.

**Dual-format API** -- Both OpenAI Chat Completions (`/v1/chat/completions`) and Anthropic Messages (`/v1/messages`) are served natively on a single port, so every major coding agent works without translation layers. See [ADR-006](adr-index.md).

**Infrastructure as Code** -- All resources are defined in Terraform with modular composition, environment-specific variable files, and automated documentation generation.

## Terraform Module Dependency Graph

The infrastructure is organized into 4 modules with explicit data dependencies. The root module (`infrastructure/main.tf`) wires them together in order.

``` mermaid
flowchart TD
    subgraph observability [Observability Module]
        O1[KMS Key<br>Log encryption]
        O2[CloudWatch Log Groups<br>Gateway + OTel]
        O3[Dashboard +<br>Saved Queries]
    end

    subgraph networking [Networking Module]
        N1[VPC<br>2 AZs, public + private]
        N2[ALB<br>TLS + Target Groups]
        N3[WAF v2<br>Managed Rules + Rate Limit]
        N4[VPC Endpoints<br>ECR, CW, SM, S3]
        N5[NAT Gateway<br>Single AZ]
    end

    subgraph auth [Auth Module]
        AU1[Cognito User Pool<br>M2M client_credentials]
        AU2[Resource Server<br>OAuth scopes]
        AU3[JWT Listener<br>validate_token action]
    end

    subgraph compute [Compute Module]
        C1[ECR Repository<br>Immutable tags, scan-on-push]
        C2[ECS Cluster + Service<br>Fargate]
        C3[Task Definition<br>Gateway + OTel sidecar]
        C4[IAM Roles<br>Execution + Task]
        C5[Secrets Manager<br>Provider API keys]
        C6[Auto Scaling<br>CPU + ALB requests]
    end

    O1 -->|logs_kms_key_arn| N3
    O2 -->|log_group_names| C3

    N2 -->|alb_arn| AU3
    N2 -->|target_group_arn| AU3
    N2 -->|target_group_arn| C2
    N2 -->|security_group_id| C2
    N1 -->|private_subnets| C2
    N2 -->|arn_suffix| C6
```

### Module Responsibilities

| Module | Resources | Outputs |
|--------|-----------|---------|
| **observability** | KMS key, CloudWatch log groups (gateway, OTel), saved queries, dashboard | `logs_kms_key_arn`, `gateway_log_group_name`, `otel_log_group_name` |
| **networking** | VPC, subnets (2 public + 2 private), NAT Gateway, VPC endpoints, ALB, WAF | `vpc_id`, `private_subnets`, `alb_arn`, `alb_dns_name`, `alb_security_group_id`, `alb_target_group_gateway_arn` |
| **auth** | Cognito User Pool, resource server, M2M client, domain, JWT listener rule | `cognito_user_pool_id`, `cognito_user_pool_arn`, `cognito_client_id`, `cognito_token_endpoint` |
| **compute** | ECR, ECS cluster, ECS service, task definition (gateway + OTel sidecar), IAM roles, Secrets Manager entries, auto-scaling policies | `ecs_cluster_name`, `ecs_service_name`, `ecr_repository_url` |

### Why This Order

1. **Observability first** -- Creates the KMS key and log groups that other modules need before they can create WAF logging or container log configurations.
2. **Networking second** -- Creates the VPC, subnets, and ALB. Needs the KMS key from observability for WAF log encryption.
3. **Auth third** -- Creates the Cognito resources and the JWT validation listener rule on the ALB. Needs the ALB ARN and target group from networking.
4. **Compute last** -- Creates the ECS cluster, service, and supporting resources. Needs private subnets and ALB from networking, and log group names from observability.

## Request Flow

``` mermaid
sequenceDiagram
    participant Agent as AI Agent
    participant ALB as ALB (TLS + WAF)
    participant JWT as ALB JWT Validator
    participant GW as Portkey Gateway
    participant Provider as Model Provider

    Agent->>ALB: POST /v1/chat/completions<br>Authorization: Bearer jwt-token
    ALB->>ALB: WAF rules check<br>(rate limit, managed rules)
    ALB->>JWT: Validate JWT
    JWT->>JWT: Verify signature (JWKS)<br>Check iss, exp, nbf, iat
    alt Token invalid
        JWT-->>Agent: 401 Unauthorized
    end
    JWT->>GW: Forward request<br>(validated claims in headers)
    GW->>GW: Parse x-portkey-* headers<br>Resolve provider + model
    GW->>Provider: Proxy request<br>(with provider API key from Secrets Manager)
    Provider-->>GW: Model response
    GW-->>ALB: Response
    ALB-->>Agent: Response
```

## Authentication Flow

``` mermaid
sequenceDiagram
    participant Client as AI Agent / Script
    participant Cognito as Cognito User Pool
    participant ALB as ALB
    participant GW as Portkey Gateway

    Client->>Cognito: POST /oauth2/token<br>grant_type=client_credentials<br>client_id + client_secret<br>scope=https://gateway.internal/invoke
    Cognito-->>Client: JWT access token (1h TTL)

    Client->>ALB: POST /v1/chat/completions<br>Authorization: Bearer jwt-token
    ALB->>ALB: validate_token action<br>Verify signature via JWKS<br>Check issuer, expiry, scope
    alt Token valid
        ALB->>GW: Forward to target group
        GW-->>ALB: Response
        ALB-->>Client: 200 OK + response body
    else Token invalid or expired
        ALB-->>Client: 401 Unauthorized
    end
```

The gateway uses **Cognito machine-to-machine (M2M)** authentication with the `client_credentials` OAuth 2.0 grant type. Key aspects:

- **Token issuance** -- Cognito issues signed JWTs with a 1-hour TTL and the `https://gateway.internal/invoke` scope.
- **ALB validation** -- The ALB's `validate_token` listener action validates JWT signatures against Cognito's JWKS endpoint, checking `iss`, `exp`, `nbf`, `iat`, and required scope claims. Invalid tokens receive a 401 directly from the ALB.
- **Zero cost** -- JWT validation is included in the ALB at no additional charge. No API Gateway or Lambda authorizer is needed.

## Network Architecture

The VPC follows a two-AZ layout optimized for cost:

- **2 public subnets** -- Host the Application Load Balancer.
- **2 private subnets** -- Host ECS Fargate tasks (Portkey gateway + OTel sidecar).
- **1 NAT Gateway** -- Handles outbound internet traffic for LLM provider API calls (non-Bedrock). Single AZ to reduce cost. See [ADR-003](adr-index.md).
- **VPC Endpoints** -- ECR (API + DKR), CloudWatch Logs, Secrets Manager, and S3 (gateway). These eliminate NAT Gateway charges for AWS service traffic.

!!! info "Bedrock resilience"
    AWS Bedrock traffic can use a VPC endpoint, making Bedrock calls immune to NAT Gateway AZ failures. Non-Bedrock provider calls (OpenAI, Anthropic, Google, Azure) require the NAT Gateway for outbound internet.

## Key Design Decisions

| Decision | Reference | Summary |
|----------|-----------|---------|
| Portkey OSS over LiteLLM | [ADR-001](adr-index.md) | LiteLLM has 14 CVEs including RCE; Portkey has zero CVEs and a ~62 MB image |
| ALB JWT over API Gateway | [ADR-005](adr-index.md) | Saves $260-2,400/month by validating JWTs at the ALB with zero additional latency |
| Dual API format | [ADR-006](adr-index.md) | Portkey natively serves both OpenAI and Anthropic formats on a single port |
| Single NAT + VPC endpoints | [ADR-003](adr-index.md) | Saves ~$32/month with acceptable HA trade-off for non-Bedrock outbound |
| 3-phase security pipeline | [ADR-004](adr-index.md) | Pre-build (hadolint + checkov), post-build (trivy + syft), post-scan (cosign) |
| AWS provider >= 6.22 | [ADR-007](adr-index.md) | Required for the `validate_token` (JWT validation) listener action on ALB |
