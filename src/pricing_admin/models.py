"""Pydantic models for pricing administration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PriceEntry(BaseModel):
    """A single pricing entry for a provider/model pair."""

    provider: str = Field(description="Provider name (e.g., 'anthropic', 'bedrock', 'openai')")
    model: str = Field(description="Model name (e.g., 'claude-sonnet-4')")
    input_per_1k: float = Field(ge=0.0, description="Cost per 1K input tokens USD")
    output_per_1k: float = Field(ge=0.0, description="Cost per 1K output tokens USD")
    cache_read_per_1k: float | None = Field(default=None, ge=0.0)
    cache_write_per_1k: float | None = Field(default=None, ge=0.0)
    updated_at: str = ""


class PriceSummary(BaseModel):
    """Summary view of a pricing entry (used in list responses)."""

    provider: str
    model: str
    input_per_1k: float
    output_per_1k: float
    source: str = "static"  # "static" or "dynamodb"
