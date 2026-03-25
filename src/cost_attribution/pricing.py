"""Token pricing table for LLM providers and models."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["PRICING_TABLE", "TokenPrice", "get_cache_savings", "get_cost"]


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
    # Bedrock
    ("bedrock", "anthropic.claude-sonnet-4-20250514-v1:0"): TokenPrice(input_per_1k=0.003, output_per_1k=0.015),
    ("bedrock", "anthropic.claude-opus-4-20250514-v1:0"): TokenPrice(input_per_1k=0.015, output_per_1k=0.075),
    ("bedrock", "anthropic.claude-3-5-sonnet-20241022-v2:0"): TokenPrice(input_per_1k=0.003, output_per_1k=0.015),
    ("bedrock", "anthropic.claude-3-5-haiku-20241022-v1:0"): TokenPrice(input_per_1k=0.001, output_per_1k=0.005),
    ("bedrock", "amazon.nova-pro-v1:0"): TokenPrice(input_per_1k=0.0008, output_per_1k=0.0032),
    ("bedrock", "amazon.nova-lite-v1:0"): TokenPrice(input_per_1k=0.00006, output_per_1k=0.00024),
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


def get_cost(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate the total cost for a request (input + output tokens)."""
    price = PRICING_TABLE.get((provider.lower(), model), _DEFAULT_PRICE)
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

    price = PRICING_TABLE.get((provider.lower(), model), _DEFAULT_PRICE)

    # Savings from reading cached tokens instead of paying full input price
    read_savings = (cache_read_tokens / 1000.0) * (price.input_per_1k - price.effective_cache_read_per_1k)

    # Extra cost from writing cache entries (above normal input price)
    write_overhead = (cache_creation_tokens / 1000.0) * (price.effective_cache_write_per_1k - price.input_per_1k)

    return max(0.0, read_savings - write_overhead)
