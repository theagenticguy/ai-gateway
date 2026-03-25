"""JWT claim extraction utilities (base64 decode only, no verification).

ALB already verifies the JWT signature before forwarding to the gateway,
so this module only needs to extract claims from the payload section.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

logger = logging.getLogger("budget_enforcement.jwt")


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode the payload section of a JWT without signature verification.

    Args:
        token: A JWT string (``header.payload.signature``).

    Returns:
        The decoded payload as a dict, or an empty dict on failure.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:  # noqa: PLR2004
            logger.debug("JWT has fewer than 2 parts")
            return {}

        payload_b64 = parts[1]
        # Restore base64 padding
        padding = 4 - len(payload_b64) % 4
        if padding != 4:  # noqa: PLR2004
            payload_b64 += "=" * padding

        decoded_bytes = base64.urlsafe_b64decode(payload_b64)
        claims: dict[str, Any] = json.loads(decoded_bytes)
        if not isinstance(claims, dict):
            return {}
    except Exception:
        logger.debug("Failed to decode JWT payload", exc_info=True)
        return {}
    else:
        return claims


def extract_team(claims: dict[str, Any]) -> str:
    """Extract team identifier from JWT claims."""
    team = claims.get("custom:team", claims.get("team", ""))
    return str(team) if team else "unknown"


def extract_user(claims: dict[str, Any]) -> str:
    """Extract user identifier from JWT claims."""
    user = claims.get("sub", claims.get("username", ""))
    return str(user) if user else "unknown"


def extract_cost_center(claims: dict[str, Any]) -> str:
    """Extract cost center from JWT claims."""
    cc = claims.get("custom:cost_center", claims.get("cost_center", ""))
    return str(cc) if cc else ""


def extract_tenant_tier(claims: dict[str, Any]) -> str:
    """Extract tenant tier from JWT claims.

    Falls back to ``"standard"`` if not present.
    """
    tier = claims.get("custom:tenant_tier", claims.get("tenant_tier", ""))
    return str(tier).lower() if tier else "standard"
