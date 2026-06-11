---
title: Model Pricing & Cost Attribution
description: "How per-token prices are sourced, why they're configurable at runtime, and how to set custom-agreement or volume-discount rates without a redeploy."
sidebar:
  order: 9
---

Cost attribution turns gateway token usage into per-team cost. Prices resolve in
two layers: a **static fallback table** baked into the cost-attribution Lambda,
and a **runtime DynamoDB overlay** that overrides it. Custom agreements, volume
discounts, and regional uplifts belong in the overlay — never in the code.

## How a price resolves

1. The Lambda loads `PRICING_TABLE` (static defaults) on cold start.
2. If `PRICING_TABLE_NAME` is set, it scans that DynamoDB table and **merges the
   entries on top of the static table** (DynamoDB wins), cached for 5 minutes.
3. `get_cost(provider, model, …)` looks up `(provider, model)`. A miss logs a
   WARNING and emits the `UnknownModelPrice` CloudWatch metric while still
   returning the `_DEFAULT_PRICE` estimate — so an unpriced model is visible and
   alarmable, never silently mis-billed.

So **the static table is only a fallback**. Anything you put in the overlay takes
precedence within 5 minutes, with no redeploy.

## Setting custom prices at runtime

Each pricing row is one DynamoDB item:

| Attribute | Value |
|---|---|
| `PK` | `PRICE#{provider}#{model}` (e.g. `PRICE#bedrock#openai.gpt-5.5`) |
| `SK` | `CONFIG` |
| `provider`, `model` | the lookup key |
| `input_per_1k`, `output_per_1k` | USD per 1K tokens |
| `cache_read_per_1k`, `cache_write_per_1k` | optional; omit if the model has no cache discount |
| `cache_supported` | optional bool; set `false` for models with no cache-billing lane (e.g. gpt-oss) so savings report 0 instead of a default 10%-of-input estimate |

Manage these through the **pricing admin API** (`/pricing`, behind the admin-plane
Cognito authorizer) — `GET`/`PUT`/`DELETE` per provider/model. That is the
supported path for an operator to record an Epic-specific negotiated rate or a
volume tier without touching code.

:::note
The pricing overlay table and the `pricing_admin` Lambda are part of the admin
plane (`enable_admin_api`). Until that is wired, prices come from the static
fallback table only. The static defaults are correct published list rates as of
their last verification — see the comments in `src/cost_attribution/pricing.py`
for per-model sources and dates.
:::

## Verified default rates (fallback)

The static OpenAI-on-Bedrock defaults were verified 2026-06-11 against the AWS
Bedrock pricing page and the AWS Price List bulk API:

| Model | Input /1K | Output /1K | Cache read /1K | Notes |
|---|---|---|---|---|
| `bedrock / openai.gpt-5.5` | $0.0055 | $0.033 | $0.00055 | AWS rate (~10% over OpenAI 1st-party list) |
| `bedrock / openai.gpt-5.4` | $0.00275 | $0.0165 | $0.000275 | AWS rate; GovCloud differs |
| `bedrock / openai.gpt-oss-120b` | $0.00015 | $0.0006 | — | no cache lane (`cache_supported=false`) |
| `bedrock / openai.gpt-oss-20b` | $0.00007 | $0.0003 | — | no cache lane |

:::caution
AWS bills the OpenAI frontier models ~10% above OpenAI's first-party list price,
and rates vary by region (GovCloud is higher). The numbers above are commercial
us-east-1 list rates and are a **fallback** — set the overlay to your actual
billed/negotiated rate for accurate chargeback.
:::

## Auto-refresh (future)

AWS exposes per-model token pricing programmatically via the **Price List bulk
API** (offer codes `AmazonBedrock` and `AmazonBedrockFoundationModels`) and the
`pricing:GetProducts` query API. A scheduled job can refresh the overlay from
these rather than hand-editing — but it must tolerate a missing row (the bulk API
lags new-model GA) by keeping the last-known rate and alerting, not failing.
