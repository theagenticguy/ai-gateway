# ADR-003: Single NAT Gateway + VPC Endpoints for Cost Optimization

**Status**: Accepted
**Date**: 2026-03-18
**Deciders**: AI Engineering NAMER

## Context

ECS Fargate tasks run in private subnets and need outbound internet access (for LLM provider API calls) and access to AWS services (ECR, CloudWatch, Secrets Manager). The standard HA pattern uses 2 NAT Gateways ($64.80/month), but VPC endpoints can handle AWS service traffic for free (S3 Gateway) or at fixed cost (Interface endpoints).

## Decision

Deploy **1 NAT Gateway** (single AZ) for outbound internet traffic + **VPC Endpoints** for ECR, CloudWatch Logs, Secrets Manager, and S3 to minimize data processing costs.

## Cost Analysis

| Approach | Monthly Cost | HA Level |
|---|---|---|
| 2 NAT GWs, no VPC endpoints | ~$65 + data processing | Full HA |
| 1 NAT GW + VPC endpoints | ~$91 ($33 NAT + $58 endpoints) | Partial (outbound only) |
| 2 NAT GWs + VPC endpoints | ~$123 ($65 NAT + $58 endpoints) | Full HA |

## Rationale

VPC endpoints eliminate NAT Gateway charges for the majority of AWS service traffic (ECR pulls, log shipping, secret fetching). The single NAT Gateway handles only outbound LLM provider API calls. During a NAT GW AZ failure, Fargate tasks remain HA (spread across 2 AZs), but outbound LLM calls from the affected AZ fail temporarily. For a medium-scale gateway, this trade-off saves $32.40/month while maintaining task-level HA.

AWS Bedrock traffic does not need the NAT Gateway at all — it can use a Bedrock VPC endpoint, making Bedrock calls resilient to NAT failures.

## Consequences

**Positive**: ~$32/month savings, reduced data processing costs for AWS service traffic, Bedrock calls immune to NAT failure.

**Negative**: Outbound LLM calls (non-Bedrock) from the NAT GW's AZ fail during AZ outage. Acceptable risk for medium scale. Upgrade to 2 NAT GWs is a single Terraform variable change.

## Addendum (2026-07): per-env NAT posture

The "upgrade is a single variable change" consequence above is now realized as a per-environment posture rather than a global setting. The `single_nat_gateway` variable (root `infrastructure/variables.tf`, threaded through the `networking` module) selects NAT topology by environment:

- **dev** keeps a **single** shared NAT gateway (`single_nat_gateway = true`, the module default). The original cost-optimized decision stands unchanged for non-prod — dev is unaffected by this addendum.
- **prod** now uses **one NAT gateway per AZ** (`single_nat_gateway = false`, set in `terragrunt/prod/terragrunt.hcl`). With 2 AZs and 2 private subnets already supplied by the root module, this yields 2 NAT gateways so egress survives a single-AZ NAT failure — outbound LLM calls no longer strand the affected AZ.

### Cost delta (reference estimate — not a billing quote)

Moving prod from 1 to 2 NAT gateways adds one NAT gateway. Using public us-east-1 list prices as a **reference estimate only** (not a verified billing figure — validate against Cost Explorer for the actual account/region): the hourly base for one NAT gateway is ~$0.045/hr, roughly **~$32/month**, plus per-GB data-processing charges on the traffic that now flows through the second gateway. Bedrock traffic remains on its VPC endpoint and does not touch either NAT gateway, so it is unaffected.

### Priority and allocation

Framed as priority rather than a hard dollar ask: **prod availability outweighs the marginal NAT cost**. Multi-AZ egress removes a known single-AZ failure mode in the production path, while dev deliberately retains the cheaper single-NAT posture. No additional owner allocation is required beyond the one Terraform variable that now carries the choice.
