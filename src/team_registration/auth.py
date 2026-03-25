"""Admin scope validation for the team registration API.

Validates that the incoming request's JWT carries the ``admin`` scope
required for team management operations.  The ALB JWT listener already
verifies signature; here we only check the scope claim.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

logger = logging.getLogger("team_registration.auth")

REQUIRED_SCOPE = "https://gateway.internal/admin"


def extract_bearer_token(event: dict[str, Any]) -> str | None:
    """Pull the bearer token from the Authorization header.

    Function URL events put headers in ``event["headers"]`` as a flat
    lower-cased dict.
    """
    headers = event.get("headers", {})
    auth_header = headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return None


def decode_jwt_claims(token: str) -> dict[str, Any]:
    """Decode JWT payload without signature verification.

    ALB has already verified the JWT before forwarding, so we only
    need to read the claims.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:  # noqa: PLR2004
            return {}
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:  # noqa: PLR2004
            payload_b64 += "=" * padding
        decoded = base64.urlsafe_b64decode(payload_b64)
        claims: dict[str, Any] = json.loads(decoded)
        return claims if isinstance(claims, dict) else {}
    except Exception:
        logger.debug("Failed to decode JWT payload", exc_info=True)
        return {}


def validate_admin_scope(event: dict[str, Any]) -> str | None:
    """Validate that the caller has the admin scope.

    Returns:
        ``None`` if the caller is authorized, or a string error message
        if authorization fails.
    """
    token = extract_bearer_token(event)
    if not token:
        return "Missing or invalid Authorization header"

    claims = decode_jwt_claims(token)
    if not claims:
        return "Unable to decode JWT claims"

    scopes_raw = claims.get("scope", "")
    scopes = scopes_raw.split() if isinstance(scopes_raw, str) else []

    if REQUIRED_SCOPE not in scopes:
        return f"Missing required scope: {REQUIRED_SCOPE}"

    return None
