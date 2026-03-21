"""Pydantic v2 request/response models for budget enforcement Lambda."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class TierConfig(BaseModel):
    """Budget defaults for a single tenant tier."""

    rpm: int = Field(ge=0, description="Requests per minute limit")
    tokens_per_day: int = Field(description="Daily token limit (-1 for unlimited)")
    monthly_usd: Decimal = Field(ge=0, description="Monthly budget in USD")


class ModelLimit(BaseModel):
    """Per-model spending limit within a team budget."""

    monthly_usd: Decimal = Field(ge=0, description="Monthly USD cap for this model")
    daily_tokens: int = Field(default=-1, description="Daily token cap for this model (-1 for unlimited)")


class BudgetCheckRequest(BaseModel):
    """Incoming request body for the budget-check endpoint."""

    jwt_token: str = Field(description="ALB-forwarded JWT (x-amzn-oidc-data header value)")
    model: str = Field(default="unknown", description="Target model being requested")
    provider: str = Field(default="unknown", description="Target provider")
    estimated_tokens: int = Field(default=0, ge=0, description="Estimated input tokens for the request")


class ModelBudgetError(BaseModel):
    """Error detail returned when a model-level budget is exceeded."""

    type: str = Field(default="model_budget_exceeded")
    model: str
    limit_usd: Decimal
    current_usd: Decimal


class BudgetStatus(BaseModel):
    """Current budget status returned by the enforcement check."""

    team: str
    user: str
    cost_center: str = ""
    tenant_tier: str = "standard"
    monthly_budget_usd: Decimal = Field(default=Decimal("1000.00"))
    current_spend_usd: Decimal = Field(default=Decimal("0.00"))
    utilization_pct: float = Field(default=0.0, ge=0.0)
    warn_threshold_pct: float = Field(default=80.0)
    hard_limit_pct: float = Field(default=100.0)


class BudgetCheckResponse(BaseModel):
    """Response from the budget enforcement Lambda."""

    allowed: bool
    status_code: int = Field(default=200)
    reason: str = Field(default="")
    budget_status: BudgetStatus | None = None
    error: ModelBudgetError | None = Field(default=None, description="Model-level budget error detail")
    retry_after_seconds: int | None = Field(
        default=None,
        description="Seconds until the budget period resets (included when blocked)",
    )
