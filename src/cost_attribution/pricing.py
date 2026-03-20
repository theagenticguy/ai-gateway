"""Token pricing table for LLM providers and models."""

from __future__ import annotations

from typing import NamedTuple

__all__ = ["PRICING_TABLE", "get_cost"]


class TokenPrice(NamedTuple):
    input_per_1k: float
    output_per_1k: float


PRICING_TABLE: dict[tuple[str, str], TokenPrice] = {
    ("anthropic", "claude-sonnet-4"): TokenPrice(0.003, 0.015),
    ("anthropic", "claude-opus-4"): TokenPrice(0.015, 0.075),
    ("anthropic", "claude-3-5-sonnet-20241022"): TokenPrice(0.003, 0.015),
    ("anthropic", "claude-3-5-haiku-20241022"): TokenPrice(0.001, 0.005),
    ("bedrock", "anthropic.claude-sonnet-4-20250514-v1:0"): TokenPrice(0.003, 0.015),
    ("bedrock", "anthropic.claude-opus-4-20250514-v1:0"): TokenPrice(0.015, 0.075),
    ("bedrock", "anthropic.claude-3-5-sonnet-20241022-v2:0"): TokenPrice(0.003, 0.015),
    ("bedrock", "anthropic.claude-3-5-haiku-20241022-v1:0"): TokenPrice(0.001, 0.005),
    ("bedrock", "amazon.nova-pro-v1:0"): TokenPrice(0.0008, 0.0032),
    ("bedrock", "amazon.nova-lite-v1:0"): TokenPrice(0.00006, 0.00024),
    ("openai", "gpt-4.1"): TokenPrice(0.002, 0.008),
    ("openai", "gpt-4.1-mini"): TokenPrice(0.0004, 0.0016),
    ("openai", "gpt-4.1-nano"): TokenPrice(0.0001, 0.0004),
    ("openai", "gpt-4o"): TokenPrice(0.0025, 0.01),
    ("openai", "gpt-4o-mini"): TokenPrice(0.00015, 0.0006),
    ("google", "gemini-2.5-pro"): TokenPrice(0.00125, 0.01),
    ("google", "gemini-2.5-flash"): TokenPrice(0.00015, 0.0006),
    ("google", "gemini-2.0-flash"): TokenPrice(0.0001, 0.0004),
}

_DEFAULT_PRICE = TokenPrice(0.01, 0.03)


def get_cost(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price = PRICING_TABLE.get((provider.lower(), model), _DEFAULT_PRICE)
    return (prompt_tokens / 1000.0) * price.input_per_1k + (completion_tokens / 1000.0) * price.output_per_1k
