---
title: "ADR-003: Single NAT Gateway + VPC Endpoints"
description: Cost-optimized networking with one NAT Gateway plus VPC endpoints for ECR, CloudWatch, Secrets Manager, and S3.
sidebar:
  order: 3
---

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

AWS Bedrock traffic does not need the NAT Gateway at all -- it can use a Bedrock VPC endpoint, making Bedrock calls resilient to NAT failures.

## Consequences

**Positive**: ~$32/month savings, reduced data processing costs for AWS service traffic, Bedrock calls immune to NAT failure.

**Negative**: Outbound LLM calls (non-Bedrock) from the NAT GW's AZ fail during AZ outage. Acceptable risk for medium scale. Upgrade to 2 NAT GWs is a single Terraform variable change.
