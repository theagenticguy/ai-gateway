"""Pydantic v2 models for the team registration API."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, EmailStr, Field


class Tier(StrEnum):
    """Supported billing tiers."""

    FREE = "free"
    STANDARD = "standard"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"


class TeamStatus(StrEnum):
    """Team lifecycle status."""

    ACTIVE = "active"
    INACTIVE = "inactive"


# ── Tier budget defaults (monthly USD) ───────────────────────────────────────

TIER_BUDGET_DEFAULTS: dict[str, int] = {
    Tier.FREE: 10,
    Tier.STANDARD: 1000,
    Tier.PREMIUM: 10000,
    Tier.ENTERPRISE: 100000,
}


# ── Request models ───────────────────────────────────────────────────────────


class RegisterTeamRequest(BaseModel):
    """POST /teams request body."""

    team_name: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    contact_email: EmailStr
    tier: Tier = Tier.STANDARD
    description: str = Field(default="", max_length=256)


# ── Response models ──────────────────────────────────────────────────────────


class UsageSummary(BaseModel):
    """Current-period usage snapshot."""

    period: str = Field(description="Current billing period (YYYY-MM)")
    total_cost_usd: float = 0.0
    monthly_budget_usd: float = 0.0
    utilization_pct: float = 0.0


class TeamResponse(BaseModel):
    """Standard team detail response."""

    team_id: str
    team_name: str
    client_id: str
    tier: str
    status: str
    description: str = ""
    contact_email: str = ""
    created_at: str
    updated_at: str = ""
    usage_summary: UsageSummary | None = None


class CredentialsResponse(BaseModel):
    """Response returned after registration or credential rotation."""

    client_id: str
    client_secret: str
    token_endpoint: str
    expires_note: str = Field(
        default="Access tokens expire after 1 hour. Use client_credentials grant to obtain new tokens."
    )


class TeamListResponse(BaseModel):
    """GET /teams response body."""

    teams: list[TeamResponse]
    count: int


class DeactivateResponse(BaseModel):
    """DELETE /teams/{id} response body."""

    team_id: str
    status: str = TeamStatus.INACTIVE
    message: str = "Team deactivated successfully. Cognito client deleted — all tokens are immediately revoked."
