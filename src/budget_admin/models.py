"""Pydantic v2 models for the Budget Admin API request/response validation."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class BudgetScope(StrEnum):
    """Supported budget scopes."""

    TEAM = "team"
    USER = "user"
    PROJECT = "project"


class BudgetPeriod(StrEnum):
    """Supported budget periods."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class TenantTier(StrEnum):
    """Supported tenant tiers."""

    FREE = "free"
    STANDARD = "standard"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"


class ModelLimit(BaseModel):
    """Per-model spend limit within a budget."""

    model: str = Field(description="Model identifier (e.g. claude-sonnet-4-20250514)")
    max_cost_usd: Decimal = Field(ge=0, description="Maximum spend for this model")


class CreateBudgetRequest(BaseModel):
    """Request body for creating a new budget."""

    scope: BudgetScope = Field(description="Budget scope: team, user, or project")
    scope_id: str = Field(min_length=1, max_length=256, description="Entity ID within scope")
    budget_usd: Decimal = Field(gt=0, le=Decimal(10000000), description="Budget limit in USD")
    token_limit: int | None = Field(default=None, ge=0, description="Optional token limit")
    period: BudgetPeriod = Field(default=BudgetPeriod.MONTHLY, description="Budget period")
    tier: TenantTier = Field(default=TenantTier.STANDARD, description="Tenant tier")
    model_limits: list[ModelLimit] = Field(default_factory=list, description="Per-model limits")
    alert_thresholds: list[int] = Field(
        default_factory=lambda: [50, 80, 100],
        description="Usage percentage thresholds that trigger alerts",
    )


class UpdateBudgetRequest(BaseModel):
    """Request body for updating a budget (partial update)."""

    budget_usd: Decimal | None = Field(default=None, gt=0, le=Decimal(10000000))
    token_limit: int | None = Field(default=None, ge=0)
    period: BudgetPeriod | None = None
    tier: TenantTier | None = None
    model_limits: list[ModelLimit] | None = None
    alert_thresholds: list[int] | None = None


class BudgetResponse(BaseModel):
    """Full budget record with optional current usage."""

    budget_id: str
    scope: str
    scope_id: str
    budget_usd: Decimal
    token_limit: int | None = None
    period: str
    tier: str
    model_limits: list[ModelLimit] = Field(default_factory=list)
    alert_thresholds: list[int] = Field(default_factory=list)
    current_usage_usd: Decimal | None = None
    current_tokens: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


class UsageResponse(BaseModel):
    """Usage record for a scope/entity."""

    scope_id: str
    period_date: str
    total_cost_usd: Decimal = Field(default=Decimal("0.00"))
    total_tokens: int = Field(default=0)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    cached_tokens: int = Field(default=0)
    request_count: int = Field(default=0)


class ListResponse(BaseModel):
    """Paginated list response."""

    items: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    last_key: dict[str, str] | None = None
