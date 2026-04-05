---
title: "ADR-012: Response Cache Strategy"
description: ElastiCache Redis for exact-match LLM response caching via Portkey's built-in cache layer.
sidebar:
  order: 12
---

**Status**: Accepted
**Date**: 2026-03-20
**Deciders**: AI Engineering NAMER

## Context

Every request to the AI Gateway currently hits upstream LLM providers directly. There is no response caching layer, which means:

- Identical prompts (common in testing, retries, and templated workflows) incur full provider costs each time.
- Latency for repeated queries is unnecessarily high.
- During load spikes, all traffic fans out to providers with no local absorption.

Portkey Gateway OSS has built-in support for response caching via a Redis backend, activated by setting `CACHE_STORE=redis` and `REDIS_URL` environment variables.

## Decision

Deploy an Amazon ElastiCache Redis cluster within the existing VPC private subnets and configure the Portkey Gateway to use it for exact-match response caching.

### Configuration

- **Engine**: Redis 7.1 (ElastiCache)
- **Node type**: `cache.t4g.micro` (single-node for dev, scalable for prod)
- **Eviction**: `allkeys-lru` (least recently used eviction when memory is full)
- **Encryption**: At-rest and in-transit (TLS) enabled
- **Network**: Private subnets only; security group allows TCP 6379 from ECS tasks only

## Options Considered

| Option | Pros | Cons |
|---|---|---|
| **ElastiCache Redis** (chosen) | Native Portkey support; sub-ms latency; mature managed service; LRU eviction built in | Additional infrastructure; no semantic matching |
| DynamoDB DAX | Serverless scaling | Not supported by Portkey; wrong access pattern for KV cache |
| CloudFront caching | Edge distribution | POST requests not cacheable by default; header-based cache keys are fragile for LLM payloads |
| No cache | Zero complexity | Every request hits providers; highest cost and latency |

## Consequences

**Positive**:
- Reduced provider costs for repeated or templated queries.
- Lower latency for cache hits (sub-millisecond Redis vs. hundreds of milliseconds for provider round-trips).
- Foundation for future semantic caching (similarity-based cache matching).

**Negative**:
- Additional infrastructure cost (~$12/month for a single `cache.t4g.micro` node).
- Cache invalidation complexity: stale responses are evicted only by LRU or TTL.
- TLS overhead on every cache read/write (negligible in practice).

## Future Enhancements

- Semantic caching: match semantically similar prompts to cached responses using embedding similarity. Portkey supports this as an upgrade path.
- Multi-node replication group with automatic failover for production workloads.
- CloudWatch metrics for cache hit rate monitoring and alerting.
