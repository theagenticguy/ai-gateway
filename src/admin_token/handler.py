"""POST /auth/token — exchange a verified SSO session for a gateway token.

Flow (ADR-016):
1. The caller presents a Cognito access token (from the SSO login). The API
   Gateway Cognito authorizer has already verified it; we re-read the claims
   via gwcore to build the principal and additionally *verify* it when a JWKS
   URL is configured (defense in depth for the non-authorizer path).
2. We mint a short-lived, audience-bound gateway JWT (HS256 over a signing
   secret from Secrets Manager) carrying the caller's team / cost_center /
   tier claims and an ``invoke`` scope. The gateway edge verifies this token
   on the inference path.
3. Every exchange emits an audit event.

The minted token is self-contained, so the inference path verifies it locally
with no round-trip to this service.
"""

from __future__ import annotations

import os
import time
from typing import Any

import boto3
import jwt
from pydantic import ValidationError

from admin_token.models import TokenExchangeRequest, TokenExchangeResponse
from gwcore import audit, auth, errors, responses
from gwcore.logging import bind, correlation_id, get_logger
from gwcore.telemetry import Timer, emit_metric

logger = get_logger("admin_token")

# Configuration (injected by Terraform as Lambda env vars).
_SIGNING_SECRET_ARN = "TOKEN_SIGNING_SECRET_ARN"  # noqa: S105 — env var NAME, not a secret value
_ISSUER = os.environ.get("TOKEN_ISSUER", "https://gateway.internal")
_JWKS_URL = os.environ.get("COGNITO_JWKS_URL", "")
_COGNITO_ISSUER = os.environ.get("COGNITO_ISSUER", "")
_MAX_TTL = 43200  # 12h hard ceiling regardless of request

_secrets_client: Any = None
_signing_secret_cache: str | None = None


def _secrets() -> Any:
    global _secrets_client  # noqa: PLW0603 — module-scoped client cache for warm Lambda
    if _secrets_client is None:
        _secrets_client = boto3.client("secretsmanager")
    return _secrets_client


def _signing_secret() -> str:
    """Fetch + cache the HS256 signing secret (warm-Lambda reuse)."""
    global _signing_secret_cache  # noqa: PLW0603 — cached secret for warm Lambda
    if _signing_secret_cache is not None:
        return _signing_secret_cache
    arn = os.environ.get(_SIGNING_SECRET_ARN, "")
    if not arn:
        msg = "Token signing secret is not configured"
        raise errors.UpstreamError(msg)
    try:
        resp = _secrets().get_secret_value(SecretId=arn)
    except Exception as exc:
        msg = "Failed to load signing secret"
        raise errors.UpstreamError(msg) from exc
    _signing_secret_cache = str(resp["SecretString"])
    return _signing_secret_cache


def _principal(event: dict[str, Any]) -> auth.Principal:
    """Build the caller principal, verifying the token when JWKS is configured."""
    if _JWKS_URL and _COGNITO_ISSUER:
        header = None
        headers = event.get("headers") or {}
        for k, v in headers.items():
            if k.lower() == "authorization":
                header = v
                break
        token = header[7:].strip() if header and header.lower().startswith("bearer ") else None
        if not token:
            msg = "Missing bearer token"
            raise errors.UnauthorizedError(msg)
        return auth.verify_token(token, jwks_url=_JWKS_URL, issuer=_COGNITO_ISSUER, token_use="access")  # noqa: S106 — Cognito token_use discriminator, not a credential
    # Authorizer-verified path: read claims only.
    return auth.build_principal(event)


def _mint(principal: auth.Principal, audience: str, ttl: int) -> tuple[str, int]:
    """Mint a short-lived gateway JWT. Returns (token, expires_in)."""
    ttl = max(300, min(ttl, _MAX_TTL))
    now = int(time.time())
    claims = {
        "iss": _ISSUER,
        "sub": principal.sub,
        "aud": audience,
        "iat": now,
        "exp": now + ttl,
        "scope": auth.INVOKE_SCOPE,
        "custom:team": principal.team,
        "custom:cost_center": principal.cost_center,
        "custom:tenant_tier": principal.tenant_tier,
    }
    token = jwt.encode(claims, _signing_secret(), algorithm="HS256")
    return token, ttl


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Lambda entry point for POST /auth/token."""
    cid = correlation_id(event)
    log = bind(logger, cid)
    try:
        with Timer("RequestLatency", route="/auth/token"):
            principal = _principal(event)

            raw_body = event.get("body") or "{}"
            try:
                req = TokenExchangeRequest.model_validate_json(raw_body)
            except ValidationError as exc:
                raise errors.ValidationFailedError("Invalid request body", details={"errors": exc.errors()}) from exc

            # A principal with no team cannot be issued a team-scoped token.
            if not principal.team:
                msg = "Caller has no team claim; cannot mint a team-scoped token"
                raise errors.ForbiddenError(msg)  # noqa: TRY301 — direct guard, not abstracted control flow

            token, expires_in = _mint(principal, req.audience.value, req.ttl_seconds)

            audit.emit(
                audit.event_from_request(
                    event,
                    action="token.exchange",
                    actor=principal.sub,
                    resource=f"audience:{req.audience.value}",
                    team=principal.team,
                    detail=f"ttl={expires_in}s",
                )
            )
            emit_metric("TokenExchange", 1, dimensions={"Audience": req.audience.value})

            body = TokenExchangeResponse(
                access_token=token,
                expires_in=expires_in,
                audience=req.audience.value,
                team=principal.team,
                scope=auth.INVOKE_SCOPE,
            )
            return responses.ok(body.model_dump())
    except errors.ControlPlaneError as exc:
        # logs the error CODE (e.g. "forbidden"), never a token
        rejection_code = exc.code
        log.info("token exchange rejected: %s", rejection_code)  # nosemgrep: python-logger-credential-disclosure
        if exc.status in {401, 403}:
            audit.emit(
                audit.event_from_request(
                    event,
                    action="token.exchange",
                    actor="unknown",
                    resource="audience:?",
                    decision="deny",
                    status=exc.status,
                    detail=exc.code,
                )
            )
        emit_metric("TokenExchangeError", 1, dimensions={"Code": exc.code})
        return responses.error_response(exc)
    except Exception:
        log.exception("Unhandled error in token exchange")
        emit_metric("TokenExchangeError", 1, dimensions={"Code": "internal_error"})
        return responses.error_response(errors.ControlPlaneError("Internal error"))
