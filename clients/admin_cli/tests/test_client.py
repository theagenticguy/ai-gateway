"""Tests for the admin CLI client — no live network, everything mocked.

Uses stdlib ``httpx.MockTransport`` (no respx dependency): one stateful handler
serves both the Cognito token endpoint (absolute URL) and the admin API (via
base_url), so we exercise token acquisition, envelope parsing, 401→refresh→retry,
and the error-envelope → GatewayError path without touching the network.
"""

from __future__ import annotations

import json

import httpx
import pytest

from admin_cli.client import Config, GatewayClient, GatewayError

TOKEN_ENDPOINT = "https://gw.auth.us-east-1.amazoncognito.com/oauth2/token"
ADMIN_URL = "https://api123.execute-api.us-east-1.amazonaws.com/prod"


def make_config(**overrides: str) -> Config:
    """A fully-populated config for tests (no env dependency)."""
    base = {
        "admin_url": ADMIN_URL,
        "client_id": "cid",
        "client_secret": "csecret",
        "token_endpoint": TOKEN_ENDPOINT,
    }
    base.update(overrides)
    return Config(**base)  # type: ignore[arg-type]


def make_client(handler) -> GatewayClient:
    """Wire a GatewayClient whose token + API HTTP both use ``handler``."""
    transport = httpx.MockTransport(handler)
    return GatewayClient(
        make_config(),
        http=httpx.Client(transport=transport, base_url=ADMIN_URL),
        token_http=httpx.Client(transport=transport),
    )


# ── config resolution ─────────────────────────────────────────────────────────


def test_config_from_env_derives_token_endpoint() -> None:
    cfg = Config.from_env(
        {
            "GATEWAY_ADMIN_URL": ADMIN_URL + "/",  # trailing slash should be stripped
            "GATEWAY_CLIENT_ID": "cid",
            "GATEWAY_CLIENT_SECRET": "csecret",
            "COGNITO_DOMAIN": "gw",
            "AWS_REGION": "us-east-1",
        }
    )
    assert cfg.admin_url == ADMIN_URL
    assert cfg.token_endpoint == TOKEN_ENDPOINT
    assert cfg.scope == "https://gateway.internal/admin"


def test_config_missing_values_raises_readable_error() -> None:
    with pytest.raises(GatewayError) as exc:
        Config.from_env({"GATEWAY_ADMIN_URL": ADMIN_URL})
    assert exc.value.code == "config_error"
    assert "GATEWAY_CLIENT_ID" in exc.value.message
    assert "GATEWAY_CLIENT_SECRET" in exc.value.message


def test_flags_override_env() -> None:
    cfg = Config.from_env(
        {"GATEWAY_ADMIN_URL": "https://env", "GATEWAY_CLIENT_ID": "envid"},
        admin_url="https://flag",
        client_id="flagid",
        client_secret="s",
        token_endpoint=TOKEN_ENDPOINT,
    )
    assert cfg.admin_url == "https://flag"
    assert cfg.client_id == "flagid"


# ── token acquisition ──────────────────────────────────────────────────────────


def test_token_acquisition_and_list_envelope() -> None:
    """Token is minted with client_credentials; a GET list envelope is parsed."""
    calls: dict[str, int] = {"token": 0, "list": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            calls["token"] += 1
            # Cognito client_credentials: Basic auth + form body.
            assert request.method == "POST"
            assert request.headers["Authorization"].startswith("Basic ")
            body = request.content.decode()
            assert "grant_type=client_credentials" in body
            assert "gateway.internal%2Fadmin" in body or "gateway.internal/admin" in body
            return httpx.Response(
                200, json={"access_token": "tok-1", "token_type": "Bearer", "expires_in": 3600}
            )
        if request.url.path == "/prod/budgets":
            calls["list"] += 1
            assert request.headers["Authorization"] == "Bearer tok-1"
            return httpx.Response(
                200,
                json={
                    "items": [{"budget_id": "b1"}, {"budget_id": "b2"}],
                    "count": 2,
                    "next_cursor": None,
                },
            )
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "nope"}})

    with make_client(handler) as client:
        result = client.request("GET", "/budgets")

    assert result["count"] == 2
    assert [i["budget_id"] for i in result["items"]] == ["b1", "b2"]
    assert result["next_cursor"] is None
    assert calls["token"] == 1  # token fetched once and cached
    assert calls["list"] == 1


def test_token_cached_across_requests() -> None:
    calls = {"token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            calls["token"] += 1
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(200, json={"items": [], "count": 0, "next_cursor": None})

    with make_client(handler) as client:
        client.request("GET", "/teams")
        client.request("GET", "/budgets")

    assert calls["token"] == 1  # cached in-memory, not re-fetched


def test_missing_access_token_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token_type": "Bearer"})  # no access_token

    with make_client(handler) as client, pytest.raises(GatewayError) as exc:
        client.request("GET", "/teams")
    assert exc.value.code == "token_error"


# ── 401 → refresh → retry ───────────────────────────────────────────────────


def test_refresh_on_401_then_retry_succeeds() -> None:
    """First API call 401s; client refreshes the token and retries once → 200."""
    state = {"token": 0, "api": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            state["token"] += 1
            return httpx.Response(
                200, json={"access_token": f"tok-{state['token']}", "expires_in": 3600}
            )
        if request.url.path == "/prod/teams":
            state["api"] += 1
            if state["api"] == 1:
                # Stale token on first hit.
                assert request.headers["Authorization"] == "Bearer tok-1"
                return httpx.Response(
                    401, json={"error": {"code": "unauthorized", "message": "expired"}}
                )
            # Retry uses the freshly minted token.
            assert request.headers["Authorization"] == "Bearer tok-2"
            return httpx.Response(200, json={"teams": [], "count": 0})
        return httpx.Response(404)

    with make_client(handler) as client:
        result = client.request("GET", "/teams")

    assert result == {"teams": [], "count": 0}
    assert state["token"] == 2  # initial fetch + one refresh
    assert state["api"] == 2  # initial 401 + retried 200


def test_persistent_401_raises_after_single_retry() -> None:
    state = {"api": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        state["api"] += 1
        return httpx.Response(401, json={"error": {"code": "unauthorized", "message": "denied"}})

    with make_client(handler) as client, pytest.raises(GatewayError) as exc:
        client.request("GET", "/teams")

    assert state["api"] == 2  # original + exactly one retry, then give up
    assert exc.value.code == "unauthorized"
    assert exc.value.status == 401


# ── error envelope → GatewayError ────────────────────────────────────────────


def test_error_envelope_surfaces_code_and_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(
            404,
            json={
                "error": {
                    "code": "not_found",
                    "message": "Team not found",
                    "details": {"team_id": "x"},
                }
            },
        )

    with make_client(handler) as client, pytest.raises(GatewayError) as exc:
        client.request("GET", "/teams/x")

    assert exc.value.code == "not_found"
    assert exc.value.message == "Team not found"
    assert exc.value.status == 404
    assert exc.value.details == {"team_id": "x"}


def test_non_envelope_error_body_falls_back() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(500, text="upstream boom")  # not the envelope shape

    with make_client(handler) as client, pytest.raises(GatewayError) as exc:
        client.request("GET", "/pricing")

    assert exc.value.status == 500
    assert "HTTP 500" in exc.value.message


def test_token_endpoint_error_surfaces() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"code": "invalid_client", "message": "bad secret"}}
        )

    with make_client(handler) as client, pytest.raises(GatewayError) as exc:
        client.request("GET", "/teams")
    assert exc.value.code == "invalid_client"


# ── request forwarding: body + params ─────────────────────────────────────────


def test_post_forwards_json_body_verbatim() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        seen["body"] = request.content.decode()
        seen["ctype"] = request.headers.get("Content-Type", "")
        return httpx.Response(201, json={"team_id": "t1"})

    payload = json.dumps({"team_name": "alpha", "tier": "standard"})
    with make_client(handler) as client:
        result = client.request("POST", "/teams", body=payload)

    assert result == {"team_id": "t1"}
    assert seen["body"] == payload  # forwarded verbatim, not re-serialized
    assert seen["ctype"] == "application/json"


def test_cursor_param_forwarded_and_none_dropped() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        seen["query"] = request.url.query.decode()
        return httpx.Response(200, json={"items": [], "count": 0, "next_cursor": None})

    with make_client(handler) as client:
        client.request("GET", "/budgets", params={"cursor": "abc123"})
        assert seen["query"] == "cursor=abc123"
        client.request("GET", "/budgets", params={"cursor": None})
        assert seen["query"] == ""  # None cursor omitted


def test_empty_body_response_parses_to_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(204)  # no content

    with make_client(handler) as client:
        assert client.request("DELETE", "/teams/x") is None
