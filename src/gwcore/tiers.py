"""Canonical tenant-tier vocabulary and quota defaults (single source of truth).

The gateway is deployed by an org for its OWN internal teams — it is not a SaaS
product — so tiers name internal quota/governance classes, not sales plans.
Every service (team registration, budget enforcement, budget admin, cost
attribution) imports ``Tier`` and ``TIER_DEFAULTS`` from here instead of
redeclaring a divergent enum.

Defaults are plain data (``dict``), not a service model: budget enforcement
layers its own ``TierConfig`` Pydantic model on top, and keeping this module
model-free avoids a cross-service import cycle. ``monthly_usd`` is an internal
chargeback/showback figure, not revenue.
"""

from __future__ import annotations

from enum import StrEnum


class Tier(StrEnum):
    """Internal quota/governance tiers, ordered lowest to highest quota."""

    SANDBOX = "sandbox"
    STANDARD = "standard"
    HIGH = "high"
    UNLIMITED = "unlimited"


DEFAULT_TENANT_TIER = Tier.STANDARD

TIER_DEFAULTS: dict[Tier, dict[str, int]] = {
    Tier.SANDBOX: {"rpm": 20, "tokens_per_day": 100000, "monthly_usd": 25},
    Tier.STANDARD: {"rpm": 100, "tokens_per_day": 500000, "monthly_usd": 100},
    Tier.HIGH: {"rpm": 500, "tokens_per_day": 5000000, "monthly_usd": 1000},
    Tier.UNLIMITED: {"rpm": 2000, "tokens_per_day": -1, "monthly_usd": 10000},
}


def monthly_budget_default(tier: str | Tier) -> int:
    """Return the default monthly USD budget for ``tier``.

    Falls back to the ``STANDARD`` default for any unknown or unmapped tier, so
    callers never need to special-case a missing key.
    """
    try:
        key = Tier(tier)
    except ValueError:
        key = Tier.STANDARD
    return TIER_DEFAULTS[key]["monthly_usd"]
