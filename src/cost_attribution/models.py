"""Pydantic v2 models for cost attribution input/output validation."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class UsageMetrics(BaseModel):
    """Token usage from a gateway log record."""

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cache_read_input_tokens: int = Field(default=0, ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def coerce_token_values(cls, data: dict) -> dict:
        """Coerce non-int values (strings, None) to int 0."""
        if not isinstance(data, dict):
            return data
        for field in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            val = data.get(field)
            if val is None:
                data[field] = 0
            elif not isinstance(val, int):
                try:
                    data[field] = int(val)
                except (ValueError, TypeError):
                    data[field] = 0
        return data

    @model_validator(mode="after")
    def compute_total(self) -> UsageMetrics:
        """Fill total_tokens from prompt + completion if zero."""
        if self.total_tokens == 0:
            self.total_tokens = self.prompt_tokens + self.completion_tokens
        return self

    @property
    def has_tokens(self) -> bool:
        return self.total_tokens > 0


class RequestHeaders(BaseModel):
    """Relevant headers from the gateway request."""

    x_portkey_provider: str = Field(default="", alias="x-portkey-provider")
    x_amzn_oidc_data: str = Field(default="", alias="x-amzn-oidc-data")

    model_config = {"populate_by_name": True}


class RequestInfo(BaseModel):
    """Nested request object from log record."""

    headers: RequestHeaders = Field(default_factory=RequestHeaders)


class LogRecord(BaseModel):
    """A parsed gateway log record containing usage and routing info."""

    usage: UsageMetrics | None = None
    model: str = Field(default="unknown")
    provider: str = Field(default="")
    req: RequestInfo = Field(default_factory=RequestInfo)

    @property
    def resolved_provider(self) -> str:
        """Resolve provider from header, then field, then 'unknown'."""
        header_provider = self.req.headers.x_portkey_provider
        if header_provider:
            return header_provider
        return self.provider or "unknown"


class MetricResult(BaseModel):
    """Extracted metric data ready for CloudWatch publishing."""

    provider: str
    model: str
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    cache_read_input_tokens: int = Field(default=0, ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)
    cache_savings_usd: float = Field(default=0.0, ge=0.0)
    cache_hit: bool = Field(default=False, description="Whether this request was served from cache")
    price_known: bool = Field(
        default=True,
        description="False when cost_usd was estimated from the default price (no pricing row).",
    )
    team: str = Field(default="unknown")
    user: str = Field(default="unknown")


class HandlerResponse(BaseModel):
    """Lambda handler response."""

    statusCode: int  # noqa: N815
    total_events: int = 0
    processed: int = 0
    skipped: int = 0
    errors: int = 0
    error: str | None = None


# ── DynamoDB record models ───────────────────────────────────────────────────


class TenantTier(StrEnum):
    """Supported tenant tiers for budget defaults."""

    FREE = "free"
    STANDARD = "standard"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"


class ModelLimit(BaseModel):
    """Per-model spending limit within a team budget (E.5)."""

    monthly_usd: Decimal = Field(default=Decimal(0), ge=0, description="Monthly USD cap for this model")
    daily_tokens: int = Field(default=-1, description="Daily token cap for this model (-1 for unlimited)")

    model_config = {"frozen": True}


class BudgetRecord(BaseModel):
    """A budget configuration stored in DynamoDB.

    PK: ``BUDGET#<team>``  SK: ``CONFIG``
    """

    pk: str = Field(description="Partition key, e.g. BUDGET#my-team")
    sk: str = Field(default="CONFIG", description="Sort key")
    team: str
    cost_center: str = Field(default="")
    tenant_tier: TenantTier = Field(default=TenantTier.STANDARD)
    monthly_budget_usd: Decimal = Field(default=Decimal("1000.00"), ge=0)
    warn_threshold_pct: Decimal = Field(
        default=Decimal("80.0"),
        ge=0,
        le=100,
        description="Percentage at which to warn (not block)",
    )
    hard_limit_pct: Decimal = Field(
        default=Decimal("100.0"),
        ge=0,
        description="Percentage at which to block requests",
    )
    model_limits: dict[str, ModelLimit] = Field(
        default_factory=dict,
        description="Per-model spending limits (E.5)",
    )
    alert_thresholds: list[int] = Field(
        default_factory=lambda: [50, 80, 100],
        description="Budget utilization percentages that trigger SNS alerts (E.6)",
    )
    alerts_sent: list[int] = Field(
        default_factory=list,
        description="Threshold percentages for which alerts have already been sent",
    )

    model_config = {"frozen": True}


class UsageRecord(BaseModel):
    """Accumulated usage for a given entity+period stored in DynamoDB.

    PK: ``USAGE#<entity_type>#<entity_id>``  SK: ``PERIOD#<YYYY-MM>``
    """

    pk: str = Field(description="Partition key, e.g. USAGE#TEAM#my-team")
    sk: str = Field(description="Sort key, e.g. PERIOD#2026-03")
    total_tokens: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    total_cost_usd: Decimal = Field(default=Decimal("0.00"), ge=0)
    request_count: int = Field(default=0, ge=0)

    model_config = {"frozen": True}
