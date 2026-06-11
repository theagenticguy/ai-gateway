"""Token pricing table for LLM providers and models."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import boto3
from pydantic import BaseModel, Field

__all__ = [
    "PRICING_TABLE",
    "TokenPrice",
    "get_cache_savings",
    "get_cost",
    "get_pricing_table",
    "is_known_model",
]

logger = logging.getLogger(__name__)


class TokenPrice(BaseModel):
    """Per-1K token pricing for a provider/model pair.

    Cache read tokens are typically ~90% cheaper than standard input.
    Cache creation (write) tokens are typically ~25% more expensive than standard input.
    """

    input_per_1k: float = Field(ge=0.0, description="Cost per 1K input tokens (USD)")
    output_per_1k: float = Field(ge=0.0, description="Cost per 1K output tokens (USD)")
    cache_read_per_1k: float | None = Field(
        default=None,
        ge=0.0,
        description="Cost per 1K cache-read input tokens (USD). Defaults to 10% of input_per_1k.",
    )
    cache_write_per_1k: float | None = Field(
        default=None,
        ge=0.0,
        description="Cost per 1K cache-write input tokens (USD). Defaults to 125% of input_per_1k.",
    )
    cache_supported: bool = Field(
        default=True,
        description=(
            "Whether this model has a prompt-cache billing lane at all. When False, "
            "cache savings are 0 regardless of token counts — a None cache_read_per_1k "
            "means 'no cache lane', NOT 'default to 10% of input'. Set False for models "
            "with no cached-token discount (e.g. gpt-oss on Bedrock)."
        ),
    )

    model_config = {"frozen": True}

    @property
    def effective_cache_read_per_1k(self) -> float:
        """Cache read price: explicit or 10% of input price."""
        if self.cache_read_per_1k is not None:
            return self.cache_read_per_1k
        return self.input_per_1k * 0.1

    @property
    def effective_cache_write_per_1k(self) -> float:
        """Cache write price: explicit or 125% of input price."""
        if self.cache_write_per_1k is not None:
            return self.cache_write_per_1k
        return self.input_per_1k * 1.25


PRICING_TABLE: dict[tuple[str, str], TokenPrice] = {
    # Anthropic (direct API)
    ("anthropic", "claude-sonnet-4"): TokenPrice(input_per_1k=0.003, output_per_1k=0.015),
    ("anthropic", "claude-opus-4"): TokenPrice(input_per_1k=0.015, output_per_1k=0.075),
    ("anthropic", "claude-3-5-sonnet-20241022"): TokenPrice(input_per_1k=0.003, output_per_1k=0.015),
    ("anthropic", "claude-3-5-haiku-20241022"): TokenPrice(input_per_1k=0.001, output_per_1k=0.005),
    # Bedrock — Anthropic Claude. Verified 2026-06-11 against the AWS Price List
    # bulk API (AmazonBedrockFoundationModels, publicationDate 2026-06-09) + the
    # per-model Bedrock model cards. Published cache-read is exactly 10% of input
    # and cache-write (5-min) exactly 125% of input for every Claude row, so we
    # leave cache fields None and rely on the effective_cache_* defaults (which
    # ARE 10%/125%) rather than restating them. These are FALLBACK rates — custom
    # agreements / volume discounts come from the DynamoDB overlay.
    #
    # Model-ID forms are NOT uniform across the 4.x line (verified): 4.8/4.7 have
    # no -v1/date; 4.6 has -v1 but no date; 4.5 has full date + -v1:0. We key on
    # the exact strings. Each model has a base ID billed at the STANDARD (regional)
    # rate and a `global.`-prefixed inference profile ~10% cheaper (the GLOBAL
    # rate); both forms are listed so a global-profile deployment is priced right
    # and does not trip UnknownModelPrice. (Opus 4.1 has no global profile.)
    # --- Claude 3.5 (legacy) ---
    ("bedrock", "anthropic.claude-3-5-sonnet-20241022-v2:0"): TokenPrice(input_per_1k=0.003, output_per_1k=0.015),
    ("bedrock", "anthropic.claude-3-5-haiku-20241022-v1:0"): TokenPrice(input_per_1k=0.0008, output_per_1k=0.004),
    # --- Claude Opus 4 / 4.1 (flat rate, no standard/global split) ---
    ("bedrock", "anthropic.claude-opus-4-20250514-v1:0"): TokenPrice(input_per_1k=0.015, output_per_1k=0.075),
    ("bedrock", "anthropic.claude-opus-4-1-20250805-v1:0"): TokenPrice(input_per_1k=0.015, output_per_1k=0.075),
    # --- Claude Opus 4.5 / 4.6 / 4.7 / 4.8 (standard rate on base ID) ---
    ("bedrock", "anthropic.claude-opus-4-5-20251101-v1:0"): TokenPrice(input_per_1k=0.0055, output_per_1k=0.0275),
    ("bedrock", "anthropic.claude-opus-4-6-v1"): TokenPrice(input_per_1k=0.0055, output_per_1k=0.0275),
    ("bedrock", "anthropic.claude-opus-4-7"): TokenPrice(input_per_1k=0.0055, output_per_1k=0.0275),
    ("bedrock", "anthropic.claude-opus-4-8"): TokenPrice(input_per_1k=0.0055, output_per_1k=0.0275),
    # global inference profiles (~10% cheaper)
    ("bedrock", "global.anthropic.claude-opus-4-6-v1"): TokenPrice(input_per_1k=0.005, output_per_1k=0.025),
    ("bedrock", "global.anthropic.claude-opus-4-7"): TokenPrice(input_per_1k=0.005, output_per_1k=0.025),
    ("bedrock", "global.anthropic.claude-opus-4-8"): TokenPrice(input_per_1k=0.005, output_per_1k=0.025),
    # --- Claude Sonnet 4 / 4.5 / 4.6 (standard rate on base ID) ---
    ("bedrock", "anthropic.claude-sonnet-4-20250514-v1:0"): TokenPrice(input_per_1k=0.003, output_per_1k=0.015),
    ("bedrock", "anthropic.claude-sonnet-4-5-20250929-v1:0"): TokenPrice(input_per_1k=0.0033, output_per_1k=0.0165),
    ("bedrock", "anthropic.claude-sonnet-4-6"): TokenPrice(input_per_1k=0.0033, output_per_1k=0.0165),
    ("bedrock", "global.anthropic.claude-sonnet-4-6"): TokenPrice(input_per_1k=0.003, output_per_1k=0.015),
    # --- Claude Haiku 4.5 (only Haiku in the 4.x line; 4.6/4.7/4.8 do not exist) ---
    ("bedrock", "anthropic.claude-haiku-4-5-20251001-v1:0"): TokenPrice(input_per_1k=0.0011, output_per_1k=0.0055),
    ("bedrock", "global.anthropic.claude-haiku-4-5-20251001-v1:0"): TokenPrice(input_per_1k=0.001, output_per_1k=0.005),
    # --- Claude Fable 5 (standard + global) ---
    ("bedrock", "anthropic.claude-fable-5"): TokenPrice(input_per_1k=0.011, output_per_1k=0.055),
    ("bedrock", "global.anthropic.claude-fable-5"): TokenPrice(input_per_1k=0.010, output_per_1k=0.050),
    # Claude Mythos 5 (anthropic.claude-mythos-5): GATED PREVIEW, mantle-only,
    # vetted-customers-only. NO published Bedrock pricing — zero entries in the
    # Price List API in any region. Intentionally omitted: if it appears in
    # traffic it SHOULD trip UnknownModelPrice rather than be billed at a guess.
    # --- Amazon Nova ---
    ("bedrock", "amazon.nova-pro-v1:0"): TokenPrice(input_per_1k=0.0008, output_per_1k=0.0032),
    ("bedrock", "amazon.nova-lite-v1:0"): TokenPrice(input_per_1k=0.00006, output_per_1k=0.00024),
    # OpenAI on Bedrock (Codex / gpt-oss lane). Verified 2026-06-11.
    # These are FALLBACK defaults; runtime prices (custom agreements, volume
    # discounts, regional uplifts) come from the DynamoDB overlay — see
    # _load_dynamic_pricing + the pricing_admin API. DDB entries override these.
    #
    # GPT-5.5 / GPT-5.4: AWS Bedrock pricing page (aws.amazon.com/bedrock/pricing).
    # AWS bills these ~10% above OpenAI's first-party list (e.g. $5.50 vs $5.00
    # /1M input for 5.5) — we bill via AWS, so use the AWS rate. Cache-read is the
    # standard 10x discount and IS billed on the mantle/Responses lane (a real
    # gpt-5.x-mantle-cache-read-tokens SKU exists in the AWS Price List API).
    # cache_write is not separately charged (OpenAI caching model) -> leave None.
    # Commercial-region 5.5/5.4 are not yet in the programmatic Price List bulk
    # API (it lagged the 2026-06-01 GA); these come from the pricing page.
    ("bedrock", "openai.gpt-5.5"): TokenPrice(input_per_1k=0.0055, output_per_1k=0.033, cache_read_per_1k=0.00055),
    ("bedrock", "openai.gpt-5.4"): TokenPrice(input_per_1k=0.00275, output_per_1k=0.0165, cache_read_per_1k=0.000275),
    # gpt-oss: AWS Price List bulk API (us-east-1). NO cached-token lane — no
    # cache SKU exists and caching is not a listed feature for these models.
    # cache_supported=False so savings compute as 0 (not the 10%-of-input default).
    ("bedrock", "openai.gpt-oss-120b"): TokenPrice(input_per_1k=0.00015, output_per_1k=0.0006, cache_supported=False),
    ("bedrock", "openai.gpt-oss-20b"): TokenPrice(input_per_1k=0.00007, output_per_1k=0.0003, cache_supported=False),
    # OpenAI
    ("openai", "gpt-4.1"): TokenPrice(input_per_1k=0.002, output_per_1k=0.008),
    ("openai", "gpt-4.1-mini"): TokenPrice(input_per_1k=0.0004, output_per_1k=0.0016),
    ("openai", "gpt-4.1-nano"): TokenPrice(input_per_1k=0.0001, output_per_1k=0.0004),
    ("openai", "gpt-4o"): TokenPrice(input_per_1k=0.0025, output_per_1k=0.01),
    ("openai", "gpt-4o-mini"): TokenPrice(input_per_1k=0.00015, output_per_1k=0.0006),
    # Google
    ("google", "gemini-2.5-pro"): TokenPrice(input_per_1k=0.00125, output_per_1k=0.01),
    ("google", "gemini-2.5-flash"): TokenPrice(input_per_1k=0.00015, output_per_1k=0.0006),
    ("google", "gemini-2.0-flash"): TokenPrice(input_per_1k=0.0001, output_per_1k=0.0004),
}

_DEFAULT_PRICE = TokenPrice(input_per_1k=0.01, output_per_1k=0.03)

# -- Dynamic pricing (DynamoDB with cache + static fallback) -------------------

_PRICING_CACHE: dict[tuple[str, str], TokenPrice] | None = None
_CACHE_TIMESTAMP: float = 0.0
_CACHE_TTL = 300  # 5 minutes


def _load_dynamic_pricing() -> dict[tuple[str, str], TokenPrice]:
    """Load pricing from DynamoDB, merging with static PRICING_TABLE.

    DynamoDB entries override static entries.
    Returns merged dict. On DDB error, returns static table only.
    """
    merged = dict(PRICING_TABLE)

    table_name = os.environ.get("PRICING_TABLE_NAME", "")
    if not table_name:
        return merged

    try:
        dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        table = dynamodb.Table(table_name)
        resp = table.scan(
            FilterExpression="SK = :sk",
            ExpressionAttributeValues={":sk": "CONFIG"},
        )
        items: list[dict[str, Any]] = resp.get("Items", [])

        for item in items:
            provider = item.get("provider", "")
            model = item.get("model", "")
            if not provider or not model:
                continue

            kwargs: dict[str, Any] = {
                "input_per_1k": float(item.get("input_per_1k", 0)),
                "output_per_1k": float(item.get("output_per_1k", 0)),
            }
            if item.get("cache_read_per_1k") is not None:
                kwargs["cache_read_per_1k"] = float(item["cache_read_per_1k"])
            if item.get("cache_write_per_1k") is not None:
                kwargs["cache_write_per_1k"] = float(item["cache_write_per_1k"])
            if item.get("cache_supported") is not None:
                kwargs["cache_supported"] = bool(item["cache_supported"])

            merged[(provider, model)] = TokenPrice(**kwargs)

        logger.debug("Loaded %d dynamic pricing entries from DynamoDB", len(items))
    except Exception:
        logger.exception("Failed to load dynamic pricing from DynamoDB; using static table only")

    return merged


def get_pricing_table() -> dict[tuple[str, str], TokenPrice]:
    """Get the effective pricing table (cached, DDB + static merged).

    If PRICING_TABLE_NAME env var is empty, returns static PRICING_TABLE.
    """
    global _PRICING_CACHE, _CACHE_TIMESTAMP  # noqa: PLW0603

    table_name = os.environ.get("PRICING_TABLE_NAME", "")
    if not table_name:
        return PRICING_TABLE

    now = time.monotonic()
    if _PRICING_CACHE is not None and (now - _CACHE_TIMESTAMP) < _CACHE_TTL:
        return _PRICING_CACHE

    _PRICING_CACHE = _load_dynamic_pricing()
    _CACHE_TIMESTAMP = now
    return _PRICING_CACHE


def is_known_model(provider: str, model: str) -> bool:
    """Whether (provider, model) has an explicit pricing row.

    False means cost is being estimated from `_DEFAULT_PRICE` — the caller
    (handler) should emit the `UnknownModelPrice` metric so unpriced models are
    visible rather than silently mis-billed.
    """
    return (provider.lower(), model) in get_pricing_table()


def _resolve_price(provider: str, model: str) -> TokenPrice:
    """Look up a price, logging a WARNING (not silently defaulting) on a miss.

    Still returns a usable number (`_DEFAULT_PRICE`) so the metric pipeline never
    breaks — but the miss is now observable in logs, and `is_known_model` lets
    the handler raise the `UnknownModelPrice` signal.
    """
    table = get_pricing_table()
    key = (provider.lower(), model)
    price = table.get(key)
    if price is None:
        logger.warning(
            "No pricing row for (%s, %s); using _DEFAULT_PRICE estimate "
            "($%.3f/$%.3f per 1k). Add a row or a DynamoDB pricing override.",
            provider,
            model,
            _DEFAULT_PRICE.input_per_1k,
            _DEFAULT_PRICE.output_per_1k,
        )
        return _DEFAULT_PRICE
    return price


def get_cost(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate the total cost for a request (input + output tokens)."""
    price = _resolve_price(provider, model)
    return (prompt_tokens / 1000.0) * price.input_per_1k + (completion_tokens / 1000.0) * price.output_per_1k


def get_cache_savings(
    provider: str,
    model: str,
    cache_read_tokens: int,
    cache_creation_tokens: int,
) -> float:
    """Calculate net savings from cache usage.

    Savings = (what cache-read tokens *would* have cost at full input price)
              minus (what they actually cost at cache-read price)
              minus (extra cost of cache-write tokens above normal input price).

    Returns a non-negative float (clamped to 0 if cache writes exceed savings).
    """
    if cache_read_tokens == 0 and cache_creation_tokens == 0:
        return 0.0

    price = _resolve_price(provider, model)

    # No cache billing lane for this model (e.g. gpt-oss on Bedrock): report 0
    # rather than fabricating savings from the 10%-of-input default.
    if not price.cache_supported:
        return 0.0

    # Savings from reading cached tokens instead of paying full input price
    read_savings = (cache_read_tokens / 1000.0) * (price.input_per_1k - price.effective_cache_read_per_1k)

    # Extra cost from writing cache entries (above normal input price)
    write_overhead = (cache_creation_tokens / 1000.0) * (price.effective_cache_write_per_1k - price.input_per_1k)

    return max(0.0, read_savings - write_overhead)
