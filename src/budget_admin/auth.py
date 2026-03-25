"""JWT admin scope validation (base64 decode only, no signature verification).

ALB already verifies the JWT signature before forwarding to the gateway,
so this module only needs to decode and check the ``scope`` claim for
admin privileges.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

logger = logging.getLogger("budget_admin.auth")


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


def validate_admin_scope(authorization: str) -> dict[str, Any] | None:
    """Validate that the JWT contains an admin scope.

    Extracts the Bearer token from the Authorization header, decodes it,
    and checks that the ``scope`` claim contains ``admin``.

    Args:
        authorization: The full Authorization header value (e.g. ``Bearer eyJ...``).

    Returns:
        The decoded claims dict if admin scope is present, or ``None`` if
        the token is missing, invalid, or lacks admin scope.
    """
    if not authorization:
        return None

    # Strip "Bearer " prefix if present
    token = authorization
    if token.lower().startswith("bearer "):
        token = token[7:]

    if not token:
        return None

    claims = decode_jwt_payload(token)
    if not claims:
        return None

    # Check scope claim — may be a space-separated string or a list
    scope = claims.get("scope", "")
    if isinstance(scope, str):
        scopes = scope.split()
    elif isinstance(scope, list):
        scopes = [str(s) for s in scope]
    else:
        scopes = []

    if "admin" in scopes:
        return claims

    # Also check custom:role or role claim as fallback
    role = claims.get("custom:role", claims.get("role", ""))
    if isinstance(role, str) and role.lower() == "admin":
        return claims

    return None
