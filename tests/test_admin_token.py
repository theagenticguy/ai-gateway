"""Tests for the admin_token exchange handler (ADR-016).

Covers: successful mint with team-scoped audience-bound JWT, TTL clamping,
no-team rejection (403), invalid body (400), unauthorized when verification
fails, audit emission on both success and denial, and the minted token's
verifiable claims.
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import MagicMock, patch

import jwt
import pytest

# Configure env BEFORE importing the handler module (module reads some at import).
os.environ["TOKEN_ISSUER"] = "https://gateway.internal"
os.environ["TOKEN_SIGNING_SECRET_ARN"] = "arn:aws:secretsmanager:us-east-1:1:secret:sign"

from admin_token import handler as h
from admin_token.models import Audience, TokenExchangeRequest

_SECRET = "test-signing-secret-value"


def _event(team: str = "ml-platform", body: dict[str, Any] | None = None) -> dict[str, Any]:
    claims = {
        "sub": "user-42",
        "scope": "https://gateway.internal/invoke",
        "custom:team": team,
        "custom:cost_center": "CC-1234",
        "custom:tenant_tier": "premium",
    }
    return {
        "requestContext": {
            "requestId": "rid-1",
            "authorizer": {"claims": claims},
            "identity": {"sourceIp": "10.0.0.1"},
        },
        "headers": {"Authorization": "Bearer header.payload.sig"},
        "body": json.dumps(body or {"audience": "claude", "ttl_seconds": 3600}),
    }


@pytest.fixture(autouse=True)
def _reset_secret_cache() -> Any:
    h._signing_secret_cache = None
    yield
    h._signing_secret_cache = None


@pytest.fixture
def _no_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the authorizer-verified (claims-only) path.
    monkeypatch.setattr(h, "_JWKS_URL", "")
    monkeypatch.setattr(h, "_COGNITO_ISSUER", "")


def _patch_secret() -> Any:
    fake = MagicMock()
    fake.get_secret_value.return_value = {"SecretString": _SECRET}
    return patch("admin_token.handler._secrets", return_value=fake)


def test_successful_exchange_mints_team_scoped_token(_no_jwks: None) -> None:
    with _patch_secret(), patch("admin_token.handler.audit.emit") as emit:
        resp = h.handler(_event())
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["audience"] == "claude"
    assert body["team"] == "ml-platform"
    assert body["expires_in"] == 3600
    assert body["token_type"] == "Bearer"
    # The minted token is a verifiable HS256 JWT with the team claims + audience.
    decoded = jwt.decode(body["access_token"], _SECRET, algorithms=["HS256"], audience="claude")
    assert decoded["custom:team"] == "ml-platform"
    assert decoded["custom:tenant_tier"] == "premium"
    assert decoded["scope"] == "https://gateway.internal/invoke"
    assert decoded["iss"] == "https://gateway.internal"
    emit.assert_called_once()  # success audit


def test_ttl_clamped_to_ceiling(_no_jwks: None) -> None:
    with _patch_secret(), patch("admin_token.handler.audit.emit"):
        resp = h.handler(_event(body={"audience": "codex", "ttl_seconds": 999999}))
    # Pydantic rejects > 43200 at validation → 400.
    assert resp["statusCode"] == 400


def test_mint_internal_clamp_via_direct_call(_no_jwks: None) -> None:
    from gwcore.auth import Principal

    with _patch_secret():
        _tok, expires = h._mint(Principal(sub="x", team="t"), "claude", 999999)
    assert expires == h._MAX_TTL  # internal clamp ceiling


def test_no_team_rejected(_no_jwks: None) -> None:
    with _patch_secret(), patch("admin_token.handler.audit.emit"):
        resp = h.handler(_event(team=""))
    assert resp["statusCode"] == 403
    assert json.loads(resp["body"])["error"]["code"] == "forbidden"


def test_invalid_body_rejected(_no_jwks: None) -> None:
    with _patch_secret(), patch("admin_token.handler.audit.emit"):
        resp = h.handler(_event(body={"audience": "not-a-real-audience"}))
    assert resp["statusCode"] == 400


def test_verify_mode_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    # Turn on verify mode; make verification fail.
    monkeypatch.setattr(h, "_JWKS_URL", "https://idp/jwks")
    monkeypatch.setattr(h, "_COGNITO_ISSUER", "https://cognito/pool")
    import gwcore.errors as gerr

    with (
        _patch_secret(),
        patch("admin_token.handler.auth.verify_token", side_effect=gerr.UnauthorizedError("bad token")),
        patch("admin_token.handler.audit.emit") as emit,
    ):
        resp = h.handler(_event())
    assert resp["statusCode"] == 401
    emit.assert_called_once()  # denial audit emitted


def test_signing_secret_cached(_no_jwks: None) -> None:
    fake = MagicMock()
    fake.get_secret_value.return_value = {"SecretString": _SECRET}
    with patch("admin_token.handler._secrets", return_value=fake):
        h._signing_secret_cache = None
        a = h._signing_secret()
        b = h._signing_secret()
    assert a == b == _SECRET
    fake.get_secret_value.assert_called_once()  # fetched once, cached warm


def test_missing_secret_arn_raises_upstream(_no_jwks: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOKEN_SIGNING_SECRET_ARN", raising=False)
    h._signing_secret_cache = None
    with patch("admin_token.handler.audit.emit"):
        resp = h.handler(_event())
    assert resp["statusCode"] == 502


def test_request_model_defaults() -> None:
    req = TokenExchangeRequest.model_validate({})
    assert req.audience == Audience.GENERIC
    assert req.ttl_seconds == 3600
