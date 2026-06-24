"""Authentication and authorization for the control plane.

Two verification modes, one ``Principal`` (ADR-016):

- ``build_principal(event)`` / ``trusted_edge`` — the API Gateway Cognito
  authorizer already verified signature + audience + expiry. We only decode
  the payload to read claims. This is the existing-handler path, unified.
- ``verify_token(token, ...)`` — full RS256 verification against the Cognito
  JWKS, for the token-exchange endpoint and any route reachable without the
  authorizer. The JWKS is fetched once and cached in-process (warm-Lambda
  reuse) with a forced refresh on unknown ``kid``.

Authorization is one declarative gate, ``require(...)``, replacing the
divergent ``"admin"`` vs ``"https://gateway.internal/admin"`` string checks in
the legacy handlers. Both legacy strings are accepted via an alias set during
migration.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import jwt
from jwt import PyJWKClient

from gwcore.cache import TTLCache
from gwcore.errors import ForbiddenError, UnauthorizedError

logger = logging.getLogger("gwcore.auth")

# Canonical admin scope plus legacy aliases accepted during migration.
ADMIN_SCOPE = "https://gateway.internal/admin"
INVOKE_SCOPE = "https://gateway.internal/invoke"
_ADMIN_SCOPE_ALIASES = frozenset({ADMIN_SCOPE, "admin"})

# JWKS cached in-process across warm invocations.
_JWKS_TTL_SECONDS = 3600.0
_jwks_cache: TTLCache[PyJWKClient] = TTLCache(default_ttl=_JWKS_TTL_SECONDS)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, normalized across both verification modes."""

    sub: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    team: str = ""
    cost_center: str = ""
    tenant_tier: str = "standard"
    client_id: str = ""
    token_use: str = ""
    raw_claims: dict[str, Any] = field(default_factory=dict)

    @property
    def is_admin(self) -> bool:
        """True if the principal carries any accepted admin scope."""
        return bool(self.scopes & _ADMIN_SCOPE_ALIASES)


def _scopes_from_claims(claims: dict[str, Any]) -> frozenset[str]:
    """Extract scopes from a ``scope`` claim (space-string or list)."""
    raw = claims.get("scope", "")
    if isinstance(raw, str):
        return frozenset(raw.split())
    if isinstance(raw, list):
        return frozenset(str(s) for s in raw)
    return frozenset()


def _principal_from_claims(claims: dict[str, Any]) -> Principal:
    """Build a ``Principal`` from a verified/decoded claim set."""
    return Principal(
        sub=str(claims.get("sub", "")),
        scopes=_scopes_from_claims(claims),
        team=str(claims.get("custom:team", "")),
        cost_center=str(claims.get("custom:cost_center", "")),
        tenant_tier=str(claims.get("custom:tenant_tier", "standard")),
        client_id=str(claims.get("client_id", claims.get("aud", ""))),
        token_use=str(claims.get("token_use", "")),
        raw_claims=claims,
    )


def decode_claims(token: str) -> dict[str, Any]:
    """Decode a JWT payload WITHOUT signature verification.

    Safe only behind the API Gateway Cognito authorizer, which has already
    verified the token. Returns ``{}`` on any decode failure.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:  # noqa: PLR2004 — JWT has 3 parts; payload is index 1
            return {}
        payload_b64 = parts[1]
        padding = (4 - len(payload_b64) % 4) % 4
        payload_b64 += "=" * padding
        decoded: Any = json.loads(base64.urlsafe_b64decode(payload_b64))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        logger.debug("Failed to decode JWT payload", exc_info=True)
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _bearer(authorization: str | None) -> str | None:
    """Strip a ``Bearer `` prefix; return the bare token or ``None``."""
    if not authorization:
        return None
    token = authorization.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token or None


def _authorization_header(event: dict[str, Any]) -> str | None:
    """Pull the Authorization header from an API Gateway / Function URL event.

    Header keys are case-insensitive across event sources, so we match loosely.
    """
    headers = event.get("headers") or {}
    for key, value in headers.items():
        if key.lower() == "authorization":
            return value
    return None


def build_principal(event: dict[str, Any]) -> Principal:
    """Build a ``Principal`` from an authorizer-verified request (trusted edge).

    Prefers the authorizer's claim context (``requestContext.authorizer.claims``)
    when present; otherwise decodes the bearer payload. Raises
    ``UnauthorizedError`` if no usable claims are found.
    """
    rc = event.get("requestContext") or {}
    authorizer = rc.get("authorizer") or {}
    claims = authorizer.get("claims")
    if isinstance(claims, dict) and claims:
        return _principal_from_claims(claims)

    token = _bearer(_authorization_header(event))
    if not token:
        msg = "Missing or invalid Authorization header"
        raise UnauthorizedError(msg)
    decoded = decode_claims(token)
    if not decoded:
        msg = "Unable to decode token claims"
        raise UnauthorizedError(msg)
    return _principal_from_claims(decoded)


def _jwks_client(jwks_url: str) -> PyJWKClient:
    """Return a cached ``PyJWKClient`` for ``jwks_url`` (warm-Lambda reuse)."""
    client = _jwks_cache.get(jwks_url)
    if client is None:
        client = PyJWKClient(jwks_url, cache_keys=True)
        _jwks_cache.set(jwks_url, client)
    return client


def verify_token(
    token: str,
    *,
    jwks_url: str,
    issuer: str,
    audience: str | None = None,
    token_use: str | None = None,
) -> Principal:
    """Fully verify a Cognito JWT (RS256) and return a ``Principal``.

    Verifies signature against the cached JWKS, plus ``iss``, ``exp``, and
    (when given) ``aud``. ``token_use`` ("access" / "id") is checked explicitly
    because Cognito access tokens do not carry ``aud``.

    Raises:
        UnauthorizedError: on any verification failure.
    """
    try:
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience,
            options={"verify_aud": audience is not None},
        )
    except jwt.PyJWTError as exc:
        logger.info("JWT verification failed: %s", exc)
        msg = "Token verification failed"
        raise UnauthorizedError(msg, details={"reason": type(exc).__name__}) from exc

    if token_use is not None and claims.get("token_use") != token_use:
        msg = "Unexpected token_use"
        raise UnauthorizedError(msg, details={"expected": token_use})

    return _principal_from_claims(claims)


def authorize(
    principal: Principal,
    *,
    scopes: list[str] | None = None,
    tiers: list[str] | None = None,
    require_all_scopes: bool = False,
) -> bool:
    """Return True if ``principal`` satisfies the scope/tier requirements.

    ``scopes`` are matched with admin-alias awareness: requiring ``ADMIN_SCOPE``
    is satisfied by the legacy ``"admin"`` scope too. Pure predicate — no raise.
    """
    if scopes:
        required = set(scopes)
        held = set(principal.scopes)
        if principal.is_admin:
            held |= _ADMIN_SCOPE_ALIASES  # admin alias satisfies admin requirement
        matches = [s in held or (s in _ADMIN_SCOPE_ALIASES and principal.is_admin) for s in required]
        scope_ok = all(matches) if require_all_scopes else any(matches)
        if not scope_ok:
            return False
    return not (tiers and principal.tenant_tier not in set(tiers))


def require(
    principal: Principal,
    *,
    scopes: list[str] | None = None,
    tiers: list[str] | None = None,
    require_all_scopes: bool = False,
) -> None:
    """Enforce scope/tier requirements, raising ``ForbiddenError`` on failure.

    The single authorization gate for the plane. Audit emission of the decision
    is the caller's responsibility (handlers wrap this with ``gwcore.audit``).
    """
    if not authorize(principal, scopes=scopes, tiers=tiers, require_all_scopes=require_all_scopes):
        msg = "Insufficient privileges"
        raise ForbiddenError(msg, details={"required_scopes": scopes or [], "required_tiers": tiers or []})
