"""Token pricing table for LLM providers and models."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["PRICING_TABLE", "TokenPrice", "get_cost"]


class TokenPrice(BaseModel):
    """Per-1K token pricing for a provider/model pair."""

    input_per_1k: float = Field(ge=0.0, description="Cost per 1K input tokens (USD)")
    output_per_1k: float = Field(ge=0.0, description="Cost per 1K output tokens (USD)")

    model_config = {"frozen": True}


PRICING_TABLE: dict[tuple[str, str], TokenPrice] = {
    ("anthropic", "claude-sonnet-4"): TokenPrice(input_per_1k=0.003, output_per_1k=0.015),
    ("anthropic", "claude-opus-4"): TokenPrice(input_per_1k=0.015, output_per_1k=0.075),
    ("anthropic", "claude-3-5-sonnet-20241022"): TokenPrice(input_per_1k=0.003, output_per_1k=0.015),
    ("anthropic", "claude-3-5-haiku-20241022"): TokenPrice(input_per_1k=0.001, output_per_1k=0.005),
    ("bedrock", "anthropic.claude-sonnet-4-20250514-v1:0"): TokenPrice(input_per_1k=0.003, output_per_1k=0.015),
    ("bedrock", "anthropic.claude-opus-4-20250514-v1:0"): TokenPrice(input_per_1k=0.015, output_per_1k=0.075),
    ("bedrock", "anthropic.claude-3-5-sonnet-20241022-v2:0"): TokenPrice(input_per_1k=0.003, output_per_1k=0.015),
    ("bedrock", "anthropic.claude-3-5-haiku-20241022-v1:0"): TokenPrice(input_per_1k=0.001, output_per_1k=0.005),
    ("bedrock", "amazon.nova-pro-v1:0"): TokenPrice(input_per_1k=0.0008, output_per_1k=0.0032),
    ("bedrock", "amazon.nova-lite-v1:0"): TokenPrice(input_per_1k=0.00006, output_per_1k=0.00024),
    ("openai", "gpt-4.1"): TokenPrice(input_per_1k=0.002, output_per_1k=0.008),
    ("openai", "gpt-4.1-mini"): TokenPrice(input_per_1k=0.0004, output_per_1k=0.0016),
    ("openai", "gpt-4.1-nano"): TokenPrice(input_per_1k=0.0001, output_per_1k=0.0004),
    ("openai", "gpt-4o"): TokenPrice(input_per_1k=0.0025, output_per_1k=0.01),
    ("openai", "gpt-4o-mini"): TokenPrice(input_per_1k=0.00015, output_per_1k=0.0006),
    ("google", "gemini-2.5-pro"): TokenPrice(input_per_1k=0.00125, output_per_1k=0.01),
    ("google", "gemini-2.5-flash"): TokenPrice(input_per_1k=0.00015, output_per_1k=0.0006),
    ("google", "gemini-2.0-flash"): TokenPrice(input_per_1k=0.0001, output_per_1k=0.0004),
}

_DEFAULT_PRICE = TokenPrice(input_per_1k=0.01, output_per_1k=0.03)


def get_cost(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price = PRICING_TABLE.get((provider.lower(), model), _DEFAULT_PRICE)
    return (prompt_tokens / 1000.0) * price.input_per_1k + (completion_tokens / 1000.0) * price.output_per_1k
