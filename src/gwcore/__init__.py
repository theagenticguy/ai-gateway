"""gwcore — shared foundation for the AI Gateway control plane.

One authentication/authorization path, a consistent response + pagination
contract, in-process caching, an append-only audit trail, structured logging,
and CloudWatch EMF + OTEL telemetry. See ADR-016.

Import-only: no network or filesystem I/O happens at import time, so adopting
gwcore does not change Lambda cold-start behavior beyond the pyjwt import.
"""

from __future__ import annotations

from gwcore.auth import Principal, authorize, build_principal, require
from gwcore.errors import (
    ConflictError,
    ControlPlaneError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationFailedError,
)
from gwcore.responses import error_response, ok, page, parse_cursor, request_body
from gwcore.tiers import DEFAULT_TENANT_TIER, TIER_DEFAULTS, Tier, monthly_budget_default

__all__ = [
    "DEFAULT_TENANT_TIER",
    "TIER_DEFAULTS",
    "ConflictError",
    "ControlPlaneError",
    "ForbiddenError",
    "NotFoundError",
    "Principal",
    "Tier",
    "UnauthorizedError",
    "ValidationFailedError",
    "authorize",
    "build_principal",
    "error_response",
    "monthly_budget_default",
    "ok",
    "page",
    "parse_cursor",
    "request_body",
    "require",
]
