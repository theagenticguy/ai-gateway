"""Tests for the gwcore control-plane foundation (ADR-016).

Covers: typed errors → HTTP envelope, response builders, ETag/304, cursor
pagination round-trip + malformed-cursor rejection, TTL cache (hit/expiry/
read-through/invalidate), principal extraction in both modes, unified RBAC
including the legacy admin-scope alias, structured logging, audit emission
(no-stream + Firehose path + best-effort swallow), and EMF metrics.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from gwcore import audit, auth, cache, errors, responses, telemetry
from gwcore import logging as gwlog

# ── errors + responses ───────────────────────────────────────────────────────


def test_error_to_body_and_response_mapping() -> None:
    exc = errors.NotFoundError("no such team", details={"team": "ghost"})
    assert exc.status == 404
    body = exc.to_body()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["details"] == {"team": "ghost"}

    resp = responses.error_response(exc)
    assert resp["statusCode"] == 404
    parsed = json.loads(resp["body"])
    assert parsed["error"]["message"] == "no such team"


@pytest.mark.parametrize(
    ("exc_cls", "status", "code"),
    [
        (errors.ValidationFailedError, 400, "validation_failed"),
        (errors.UnauthorizedError, 401, "unauthorized"),
        (errors.ForbiddenError, 403, "forbidden"),
        (errors.NotFoundError, 404, "not_found"),
        (errors.ConflictError, 409, "conflict"),
        (errors.UpstreamError, 502, "upstream_error"),
    ],
)
def test_error_hierarchy_status_codes(exc_cls: type[errors.ControlPlaneError], status: int, code: str) -> None:
    exc = exc_cls()
    assert exc.status == status
    assert exc.code == code


def test_ok_response_default() -> None:
    resp = responses.ok({"hello": "world"})
    assert resp["statusCode"] == 200
    assert resp["headers"]["Content-Type"] == "application/json"
    assert json.loads(resp["body"]) == {"hello": "world"}


def test_ok_cache_control_header() -> None:
    resp = responses.ok({"x": 1}, cache_seconds=60)
    assert resp["headers"]["Cache-Control"] == "private, max-age=60"


def test_etag_and_304_round_trip() -> None:
    body = {"a": 1, "b": [2, 3]}
    first = responses.ok(body, etag=True)
    tag = first["headers"]["ETag"]
    assert tag.startswith('"')
    # Same body + matching If-None-Match → 304 with empty body.
    second = responses.ok(body, etag=True, if_none_match=tag)
    assert second["statusCode"] == 304
    assert second["body"] == ""


def test_etag_is_stable_regardless_of_key_order() -> None:
    assert responses.etag_for({"a": 1, "b": 2}) == responses.etag_for({"b": 2, "a": 1})


def test_cursor_round_trip() -> None:
    key = {"pk": "BUDGET#ml", "sk": "CONFIG"}
    cursor = responses.encode_cursor(key)
    assert cursor is not None
    assert responses.parse_cursor(cursor) == key


def test_encode_cursor_none() -> None:
    assert responses.encode_cursor(None) is None
    assert responses.encode_cursor({}) is None
    assert responses.parse_cursor(None) is None


def test_parse_cursor_malformed_raises() -> None:
    with pytest.raises(errors.ValidationFailedError):
        responses.parse_cursor("!!!not-base64!!!")
    # Valid base64 but not a JSON object.
    bad = base64.urlsafe_b64encode(b'"a string"').decode()
    with pytest.raises(errors.ValidationFailedError):
        responses.parse_cursor(bad)


def test_page_response() -> None:
    resp = responses.page([{"id": 1}, {"id": 2}], {"pk": "X"})
    body = json.loads(resp["body"])
    assert body["count"] == 2
    assert body["next_cursor"] is not None
    assert len(body["items"]) == 2


# ── cache ────────────────────────────────────────────────────────────────────


def test_cache_hit_and_miss() -> None:
    c: cache.TTLCache[str] = cache.TTLCache(default_ttl=100.0)
    assert c.get("k") is None
    c.set("k", "v")
    assert c.get("k") == "v"


def test_cache_expiry_with_fake_clock() -> None:
    now = [1000.0]
    c: cache.TTLCache[str] = cache.TTLCache(default_ttl=10.0, clock=lambda: now[0])
    c.set("k", "v")
    now[0] = 1009.0
    assert c.get("k") == "v"  # not yet expired
    now[0] = 1011.0
    assert c.get("k") is None  # expired


def test_cache_read_through_loads_once() -> None:
    c: cache.TTLCache[int] = cache.TTLCache(default_ttl=100.0)
    calls = {"n": 0}

    def loader() -> int:
        calls["n"] += 1
        return 42

    assert c.read_through("k", loader) == 42
    assert c.read_through("k", loader) == 42
    assert calls["n"] == 1  # loader ran exactly once


def test_cache_read_through_does_not_cache_on_loader_error() -> None:
    c: cache.TTLCache[int] = cache.TTLCache()

    def boom() -> int:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        c.read_through("k", boom)
    assert c.get("k") is None


def test_cache_invalidate_and_clear() -> None:
    c: cache.TTLCache[str] = cache.TTLCache()
    c.set("a", "1")
    c.set("b", "2")
    c.invalidate("a")
    assert c.get("a") is None
    assert c.get("b") == "2"
    c.clear()
    assert c.get("b") is None


# ── auth: principal + RBAC ─────────────────────────────────────────────────────


def _claims(scope: Any = "", **extra: Any) -> dict[str, Any]:
    return {"sub": "user-1", "scope": scope, **extra}


def test_build_principal_from_authorizer_claims() -> None:
    event = {
        "requestContext": {
            "authorizer": {
                "claims": _claims(
                    "https://gateway.internal/invoke",
                    **{"custom:team": "ml-platform", "custom:tenant_tier": "premium"},
                )
            }
        }
    }
    p = auth.build_principal(event)
    assert p.sub == "user-1"
    assert p.team == "ml-platform"
    assert p.tenant_tier == "premium"
    assert "https://gateway.internal/invoke" in p.scopes


def test_build_principal_from_bearer_when_no_authorizer() -> None:
    payload = base64.urlsafe_b64encode(json.dumps(_claims("admin")).encode()).rstrip(b"=").decode()
    token = f"hdr.{payload}.sig"
    event = {"headers": {"Authorization": f"Bearer {token}"}}
    p = auth.build_principal(event)
    assert p.sub == "user-1"
    assert p.is_admin


def test_build_principal_missing_auth_raises() -> None:
    with pytest.raises(errors.UnauthorizedError):
        auth.build_principal({"headers": {}})


def test_scope_list_form_supported() -> None:
    p = auth._principal_from_claims(_claims(["a", "b"]))
    assert p.scopes == frozenset({"a", "b"})


def test_admin_alias_legacy_and_canonical() -> None:
    legacy = auth._principal_from_claims(_claims("admin"))
    canonical = auth._principal_from_claims(_claims("https://gateway.internal/admin"))
    assert legacy.is_admin
    assert canonical.is_admin
    # Requiring the canonical admin scope is satisfied by BOTH (the bug-fix).
    assert auth.authorize(legacy, scopes=[auth.ADMIN_SCOPE])
    assert auth.authorize(canonical, scopes=[auth.ADMIN_SCOPE])


def test_authorize_scope_and_tier() -> None:
    p = auth._principal_from_claims(_claims("scope-x", **{"custom:tenant_tier": "premium"}))
    assert auth.authorize(p, scopes=["scope-x"])
    assert not auth.authorize(p, scopes=["scope-y"])
    assert auth.authorize(p, tiers=["premium", "enterprise"])
    assert not auth.authorize(p, tiers=["free"])


def test_authorize_require_all_scopes() -> None:
    p = auth._principal_from_claims(_claims("a b"))
    assert auth.authorize(p, scopes=["a", "b"], require_all_scopes=True)
    assert not auth.authorize(p, scopes=["a", "c"], require_all_scopes=True)
    assert auth.authorize(p, scopes=["a", "c"], require_all_scopes=False)


def test_require_raises_forbidden() -> None:
    p = auth._principal_from_claims(_claims("nothing"))
    with pytest.raises(errors.ForbiddenError):
        auth.require(p, scopes=[auth.ADMIN_SCOPE])
    # An admin passes.
    admin = auth._principal_from_claims(_claims("admin"))
    auth.require(admin, scopes=[auth.ADMIN_SCOPE])  # no raise


def test_decode_claims_garbage_returns_empty() -> None:
    assert auth.decode_claims("not-a-jwt") == {}
    assert auth.decode_claims("") == {}


# ── auth: verify_token (mocked JWKS) ───────────────────────────────────────────


def test_verify_token_success() -> None:
    fake_claims = _claims("admin", token_use="access", sub="u9")
    with (
        patch("gwcore.auth._jwks_client") as jwks,
        patch("gwcore.auth.jwt.decode", return_value=fake_claims) as decode,
    ):
        jwks.return_value.get_signing_key_from_jwt.return_value = MagicMock(key="KEY")
        p = auth.verify_token("tok", jwks_url="https://idp/jwks", issuer="iss", token_use="access")
    assert p.sub == "u9"
    assert p.is_admin
    decode.assert_called_once()


def test_verify_token_bad_signature_raises_unauthorized() -> None:
    import jwt as pyjwt

    with (
        patch("gwcore.auth._jwks_client") as jwks,
        patch("gwcore.auth.jwt.decode", side_effect=pyjwt.InvalidSignatureError("bad")),
    ):
        jwks.return_value.get_signing_key_from_jwt.return_value = MagicMock(key="KEY")
        with pytest.raises(errors.UnauthorizedError):
            auth.verify_token("tok", jwks_url="https://idp/jwks", issuer="iss")


def test_verify_token_wrong_token_use_raises() -> None:
    with (
        patch("gwcore.auth._jwks_client") as jwks,
        patch("gwcore.auth.jwt.decode", return_value=_claims("admin", token_use="id")),
    ):
        jwks.return_value.get_signing_key_from_jwt.return_value = MagicMock(key="KEY")
        with pytest.raises(errors.UnauthorizedError):
            auth.verify_token("tok", jwks_url="https://idp/jwks", issuer="iss", token_use="access")


def test_jwks_client_is_cached() -> None:
    auth._jwks_cache.clear()
    with patch("gwcore.auth.PyJWKClient") as ctor:
        ctor.return_value = MagicMock()
        a = auth._jwks_client("https://idp/jwks")
        b = auth._jwks_client("https://idp/jwks")
    assert a is b
    ctor.assert_called_once()  # constructed once, reused warm


# ── logging ────────────────────────────────────────────────────────────────────


def test_json_formatter_emits_correlation_and_fields() -> None:
    logger = gwlog.get_logger("test.logger")
    record = logger.makeRecord(
        "test.logger", 20, "f", 1, "hello", None, None, extra={"correlation_id": "rid-1", "fields": {"k": "v"}}
    )
    out = gwlog.JsonFormatter().format(record)
    parsed = json.loads(out)
    assert parsed["message"] == "hello"
    assert parsed["correlation_id"] == "rid-1"
    assert parsed["k"] == "v"


def test_correlation_id_extraction() -> None:
    assert gwlog.correlation_id({"requestContext": {"requestId": "abc"}}) == "abc"
    assert gwlog.correlation_id({}) == ""


def test_get_logger_idempotent_handlers() -> None:
    a = gwlog.get_logger("idempotent.test")
    n = len(a.handlers)
    b = gwlog.get_logger("idempotent.test")
    assert a is b
    assert len(b.handlers) == n  # no duplicate handler on warm reuse


# ── audit ───────────────────────────────────────────────────────────────────────


def test_audit_emit_no_stream_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUDIT_FIREHOSE_STREAM", raising=False)
    ev = audit.AuditEvent(action="budget.update", actor="ml", resource="tm_1")
    assert audit.emit(ev) is False  # logged + dropped, never raises


def test_audit_emit_to_firehose() -> None:
    ev = audit.AuditEvent(action="team.create", actor="admin", resource="tm_2", status=201)
    fake = MagicMock()
    with patch("gwcore.audit._client", return_value=fake):
        assert audit.emit(ev, stream_name="audit-stream") is True
    fake.put_record.assert_called_once()
    kwargs = fake.put_record.call_args.kwargs
    assert kwargs["DeliveryStreamName"] == "audit-stream"
    assert b"team.create" in kwargs["Record"]["Data"]


def test_audit_emit_swallows_firehose_error() -> None:
    ev = audit.AuditEvent(action="x", actor="y", resource="z")
    fake = MagicMock()
    fake.put_record.side_effect = RuntimeError("firehose down")
    with patch("gwcore.audit._client", return_value=fake):
        assert audit.emit(ev, stream_name="s") is False  # best-effort, no raise


def test_event_from_request_extracts_ip_and_correlation() -> None:
    req = {"requestContext": {"requestId": "rid-9", "identity": {"sourceIp": "10.0.0.5"}}}
    ev = audit.event_from_request(req, action="a", actor="b", resource="c", team="t")
    assert ev.source_ip == "10.0.0.5"
    assert ev.correlation_id == "rid-9"
    assert ev.team == "t"


# ── telemetry ──────────────────────────────────────────────────────────────────


def test_emit_metric_structure(capsys: pytest.CaptureFixture[str]) -> None:
    doc = telemetry.emit_metric("RequestCount", 1, dimensions={"Route": "/budgets"})
    assert doc["RequestCount"] == 1
    assert doc["Route"] == "/budgets"
    assert doc["_aws"]["CloudWatchMetrics"][0]["Metrics"][0]["Name"] == "RequestCount"
    # Printed as a single JSON line for CloudWatch EMF ingestion.
    printed = capsys.readouterr().out.strip()
    assert json.loads(printed)["RequestCount"] == 1


def test_timer_emits_latency(capsys: pytest.CaptureFixture[str]) -> None:
    ticks = [100.0, 100.25]
    with telemetry.Timer("RequestLatency", clock=lambda: ticks.pop(0), route="/x"):
        pass
    doc = json.loads(capsys.readouterr().out.strip())
    assert doc["RequestLatency"] == pytest.approx(250.0)  # 0.25s → 250ms


def test_genai_attributes() -> None:
    attrs = telemetry.genai_attributes(
        operation="guardrail.check", model="claude-sonnet-4-6", provider="bedrock", input_tokens=10
    )
    assert attrs["gen_ai.operation.name"] == "guardrail.check"
    assert attrs["gen_ai.request.model"] == "claude-sonnet-4-6"
    assert attrs["gen_ai.usage.input_tokens"] == 10
    assert "gen_ai.usage.output_tokens" not in attrs
