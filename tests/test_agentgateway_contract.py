"""Tests for the agentgateway guardrail-webhook contract path (ADR-017).

Covers the contract budget_enforcement speaks: request parsing from
agentgateway's ``{body:{messages}}`` + forwarded headers, and the ``action``
response envelope.
"""

from __future__ import annotations

import base64
import json

from gwcore import agentgateway


def _jwt(claims: dict) -> str:
    """Build an unsigned JWT-shaped token (header.payload.sig) for tests."""
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{payload}.sig"


# ── gwcore.agentgateway helpers ──────────────────────────────────────────────


def test_extract_messages_and_text():
    body = {
        "body": {
            "messages": [
                {"role": "system", "content": "be nice"},
                {"role": "user", "content": [{"type": "text", "text": "hello"}, {"type": "image"}]},
                "garbage",
            ]
        }
    }
    msgs = agentgateway.extract_messages(body)
    assert len(msgs) == 2  # the bare string is dropped
    assert agentgateway.messages_to_text(msgs) == "be nice\nhello"


def test_estimate_tokens_is_nonzero_for_text():
    msgs = [{"role": "user", "content": "x" * 40}]
    assert agentgateway.estimate_tokens(msgs) == 10  # 40 chars / 4


def test_header_lookup_case_insensitive():
    event = {"headers": {"X-Amzn-Oidc-Data": "tok"}}
    assert agentgateway.header_lookup(event, "x-amzn-oidc-data") == "tok"
    assert agentgateway.header_lookup(event, "missing") == ""


def test_action_builders():
    assert json.loads(agentgateway.pass_action()["body"]) == {"action": {"pass": {}}}
    rej = json.loads(agentgateway.reject_action(429, "b", "r")["body"])
    assert rej["action"]["reject"] == {"status_code": 429, "body": "b", "reason": "r"}
    mask = json.loads(agentgateway.mask_action([{"role": "user", "content": "***"}])["body"])
    assert mask["action"]["mask"]["body"]["messages"][0]["content"] == "***"


# ── budget_enforcement via agentgateway ──────────────────────────────────────


def test_budget_handler_agentgateway_allow(monkeypatch):
    from budget_enforcement import handler as be

    # Force the budget check to allow without touching DynamoDB.
    monkeypatch.setattr(be, "_check_budget", lambda req: be.BudgetCheckResponse(allowed=True, reason="ok"))
    event = {
        "headers": {"x-amzn-oidc-data": _jwt({"custom:team": "alpha", "sub": "u1"})},
        "body": json.dumps({"body": {"messages": [{"role": "user", "content": "hello world"}]}}),
    }
    resp = be.handler(event)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == {"action": {"pass": {}}}


def test_budget_handler_agentgateway_deny_maps_to_reject(monkeypatch):
    from budget_enforcement import handler as be

    monkeypatch.setattr(
        be,
        "_check_budget",
        lambda req: be.BudgetCheckResponse(
            allowed=False, status_code=429, reason="Monthly budget exceeded", retry_after_seconds=3600
        ),
    )
    event = {
        "headers": {"x-amzn-oidc-data": _jwt({"custom:team": "alpha"})},
        "body": json.dumps({"body": {"messages": [{"role": "user", "content": "spend"}]}}),
    }
    resp = be.handler(event)
    assert resp["statusCode"] == 200
    action = json.loads(resp["body"])["action"]
    assert action["reject"]["status_code"] == 429
    inner = json.loads(action["reject"]["body"])
    assert inner["retry_after_seconds"] == 3600
    assert "budget" in inner["error"].lower()
