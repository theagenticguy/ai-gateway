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
from gwcore.responses import error_response, ok, page, parse_cursor

__all__ = [
    "ConflictError",
    "ControlPlaneError",
    "ForbiddenError",
    "NotFoundError",
    "Principal",
    "UnauthorizedError",
    "ValidationFailedError",
    "authorize",
    "build_principal",
    "error_response",
    "ok",
    "page",
    "parse_cursor",
    "require",
]
