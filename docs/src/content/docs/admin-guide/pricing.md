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

### Anthropic Claude on Bedrock (verified 2026-06-11)

Standard (regional) rate on the base model ID; the `global.`-prefixed inference
profile is ~10% cheaper and has its own row. Published cache-read is exactly 10%
of input and cache-write (5-min) exactly 125%, so the defaults compute correct
cache savings without per-row cache fields.

| Model | Base ID | Input /1K | Output /1K |
|---|---|---|---|
| Opus 4.8 | `anthropic.claude-opus-4-8` | $0.0055 | $0.0275 |
| Opus 4.7 | `anthropic.claude-opus-4-7` | $0.0055 | $0.0275 |
| Opus 4.6 | `anthropic.claude-opus-4-6-v1` | $0.0055 | $0.0275 |
| Opus 4.5 | `anthropic.claude-opus-4-5-20251101-v1:0` | $0.0055 | $0.0275 |
| Opus 4.1 / 4 | `anthropic.claude-opus-4-1-...` / `-4-...` | $0.015 | $0.075 |
| Sonnet 4.6 | `anthropic.claude-sonnet-4-6` | $0.0033 | $0.0165 |
| Sonnet 4.5 | `anthropic.claude-sonnet-4-5-20250929-v1:0` | $0.0033 | $0.0165 |
| Sonnet 4 | `anthropic.claude-sonnet-4-20250514-v1:0` | $0.003 | $0.015 |
| Haiku 4.5 | `anthropic.claude-haiku-4-5-20251001-v1:0` | $0.0011 | $0.0055 |
| Fable 5 | `anthropic.claude-fable-5` | $0.011 | $0.055 |

:::note
**Model-ID forms are not uniform** across the 4.x line — 4.8/4.7 carry no
`-v1`/date, 4.6 has `-v1` but no date, 4.5 and older carry the full date — so the
table keys on exact strings, not a date-stamp pattern. **Haiku exists only at
4.5** in the 4.x line. **Claude Mythos 5** (`anthropic.claude-mythos-5`) is a
gated preview with **no published price** — intentionally omitted so it trips
`UnknownModelPrice` rather than billing at a guess.
:::

## Auto-refresh (future)

AWS exposes per-model token pricing programmatically via the **Price List bulk
API** (offer codes `AmazonBedrock` and `AmazonBedrockFoundationModels`) and the
`pricing:GetProducts` query API. A scheduled job can refresh the overlay from
these rather than hand-editing — but it must tolerate a missing row (the bulk API
lags new-model GA) by keeping the last-known rate and alerting, not failing.
