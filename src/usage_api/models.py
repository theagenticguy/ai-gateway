"""Pydantic models for the usage self-service API."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class UsagePeriod(BaseModel):
    """Usage data for a single billing period."""

    period: str = Field(description="Period in YYYY-MM format")
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    total_cost_usd: Decimal = Decimal("0.00")
    request_count: int = 0


class ModelUsage(BaseModel):
    """Usage data for a single model within a team's current period."""

    model: str
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost_usd: Decimal = Decimal("0.00")
    request_count: int = 0


class UsageResponse(BaseModel):
    """Top-level response for the usage API."""

    team: str
    current_period: UsagePeriod | None = None
    history: list[UsagePeriod] = []
    models: list[ModelUsage] = []
    budget_utilization_pct: float | None = None
    monthly_budget_usd: Decimal | None = None
