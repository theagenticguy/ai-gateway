"""Pydantic v2 models for cost attribution input/output validation."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, model_validator

from gwcore.tiers import Tier as TenantTier


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

    x_amzn_oidc_data: str = Field(default="", alias="x-amzn-oidc-data")

    model_config = {"populate_by_name": True}


class RequestInfo(BaseModel):
    """Nested request object from log record."""

    headers: RequestHeaders = Field(default_factory=RequestHeaders)


class LogRecord(BaseModel):
    """A parsed agentgateway access-log record containing usage and routing info.

    agentgateway emits a flat top-level shape (ADR-017): flat token fields
    (``prompt_tokens`` / ``completion_tokens`` / ``total_tokens``,
    ``cached_input_tokens`` / ``cache_creation_input_tokens``), ``model``,
    ``provider``, and the ALB JWT as a flat ``oidc_data`` field. The
    access-log ``add`` block emits these flat keys, so the nested ``usage``
    block is synthesized here for downstream pricing.
    """

    usage: UsageMetrics | None = None
    model: str = Field(default="unknown")
    provider: str = Field(default="")
    req: RequestInfo = Field(default_factory=RequestInfo)
    # agentgateway flat identity field (CEL: request.headers["x-amzn-oidc-data"]).
    oidc_data: str = Field(default="", description="ALB JWT when the access log emits it flat")

    @model_validator(mode="before")
    @classmethod
    def synthesize_nested_from_flat(cls, data: Any) -> Any:
        """Build a nested ``usage`` block from agentgateway flat token fields.

        Runs only when no nested ``usage`` is present but flat token fields are.
        """
        if not isinstance(data, dict):
            return data
        flat_token_keys = (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "cache_creation_input_tokens",
        )
        if data.get("usage") is None and any(k in data for k in flat_token_keys):
            data = dict(data)
            data["usage"] = {
                # agentgateway's access log re-keys CEL inputTokens/outputTokens
                # to prompt_tokens/completion_tokens; accept either spelling.
                "prompt_tokens": data.get("prompt_tokens", data.get("input_tokens", 0)),
                "completion_tokens": data.get("completion_tokens", data.get("output_tokens", 0)),
                "total_tokens": data.get("total_tokens", 0),
                "cache_read_input_tokens": data.get("cached_input_tokens", 0),
                "cache_creation_input_tokens": data.get("cache_creation_input_tokens", 0),
            }
        return data

    @property
    def resolved_provider(self) -> str:
        """Resolve provider from the flat ``provider`` field, else 'unknown'."""
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


class ModelLimit(BaseModel):
    """Per-model spending limit within a team budget (E.5)."""

    monthly_usd: Decimal = Field(default=Decimal(0), ge=0, description="Monthly USD cap for this model")
    daily_tokens: int = Field(default=-1, description="Daily token cap for this model (-1 for unlimited)")

    model_config = {"frozen": True}


class BudgetRecord(BaseModel):
    """A budget configuration stored in the ``gateway-budgets`` table.

    Keyed by the real Terraform schema (issue #261): hash=``budget_id`` (uuid),
    range=``scope`` (always ``"CONFIG"`` for config rows). The entity kind is in
    ``scope_type`` and the entity id in ``scope_id``; a lookup by team goes
    through the ``scope-index`` GSI (HASH=``scope``, RANGE=``scope_id``).
    """

    budget_id: str = Field(description="Partition key, a uuid")
    scope: str = Field(default="CONFIG", description="Sort key; 'CONFIG' for budget config rows")
    scope_type: str = Field(default="team", description="Entity kind: team | user | project")
    scope_id: str = Field(description="Entity id, e.g. the team name")
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
    """Accumulated usage for a given entity+period in the ``gateway-usage`` table.

    Keyed by the real Terraform schema (issue #261): hash=``scope_id``,
    range=``period_date``. Team monthly rows use ``scope_id = "team#<team>"``,
    ``period_date = "YYYY-MM"``; per-model rows use
    ``scope_id = "team#<team>#model#<model>"``.
    """

    scope_id: str = Field(description="Partition key, e.g. team#my-team")
    period_date: str = Field(description="Sort key, e.g. 2026-03")
    total_tokens: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    total_cost_usd: Decimal = Field(default=Decimal("0.00"), ge=0)
    request_count: int = Field(default=0, ge=0)

    model_config = {"frozen": True}
