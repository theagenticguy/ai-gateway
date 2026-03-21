"""Pydantic v2 models for monthly chargeback report generation."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic needs runtime access
from decimal import Decimal

from pydantic import BaseModel, Field


class ReportRequest(BaseModel):
    """Input payload from Step Functions."""

    month: str = Field(
        description="Target month in YYYY-MM format",
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    )
    output_format: str = Field(default="html", description="Report output format")
    send_email: bool = Field(default=False, description="Whether to send report via email")


class TeamUsageSummary(BaseModel):
    """Aggregated usage data for a single team."""

    team: str
    total_cost_usd: Decimal = Field(default=Decimal("0.00"), ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    request_count: int = Field(default=0, ge=0)
    budget_limit_usd: Decimal | None = Field(default=None, description="Monthly budget limit, None if uncapped")
    budget_utilization_pct: Decimal | None = Field(default=None, description="Percentage of budget consumed")
    top_model: str = Field(default="N/A", description="Most-used model by cost")
    top_model_cost_usd: Decimal = Field(default=Decimal("0.00"), ge=0)

    @property
    def is_over_budget(self) -> bool:
        """Return True if budget utilization exceeds 100%."""
        if self.budget_utilization_pct is None:
            return False
        return self.budget_utilization_pct > Decimal("100.0")


class ReportData(BaseModel):
    """Full chargeback report payload used by the HTML renderer."""

    month: str
    generated_at: datetime
    teams: list[TeamUsageSummary] = Field(default_factory=list)
    total_cost_usd: Decimal = Field(default=Decimal("0.00"), ge=0)
    total_tokens: int = Field(default=0, ge=0)
    total_requests: int = Field(default=0, ge=0)
    team_count: int = Field(default=0, ge=0)
    previous_month_cost_usd: Decimal | None = Field(
        default=None,
        description="Total cost from the previous month for MoM comparison",
    )

    @property
    def month_over_month_change_pct(self) -> Decimal | None:
        """Calculate month-over-month cost change percentage."""
        if self.previous_month_cost_usd is None:
            return None
        if self.previous_month_cost_usd == 0:
            return None
        return ((self.total_cost_usd - self.previous_month_cost_usd) / self.previous_month_cost_usd) * 100


class ReportResponse(BaseModel):
    """Lambda return payload for Step Functions."""

    s3_url: str
    summary: str
    team_count: int = Field(ge=0)
    total_cost_usd: Decimal = Field(ge=0)
    month: str
