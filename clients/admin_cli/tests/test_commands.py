"""Tests for the cyclopts command layer — body building + CLI dispatch/exit codes.

The command tree is driven with token lists (``app.meta([...])``) and the
network is stubbed by monkeypatching ``admin_cli.commands._client`` — no real
HTTP or AWS calls.
"""

from __future__ import annotations

import json

import pytest

from admin_cli import commands
from admin_cli.client import GatewayError

# ── build_body ─────────────────────────────────────────────────────────────


def test_build_body_none_when_empty() -> None:
    assert commands.build_body(None, []) is None


def test_build_body_inline_json() -> None:
    out = commands.build_body('{"a": 1}', [])
    assert json.loads(out) == {"a": 1}


def test_build_body_from_file(tmp_path) -> None:
    f = tmp_path / "team.json"
    f.write_text('{"team_name": "alpha"}', encoding="utf-8")
    out = commands.build_body(f"@{f}", [])
    assert json.loads(out) == {"team_name": "alpha"}


def test_build_body_set_overlays_and_coerces_types() -> None:
    out = commands.build_body(
        '{"tier": "standard"}', ["budget_usd=1000", "team_name=alpha", "active=true"]
    )
    parsed = json.loads(out)
    assert parsed == {"tier": "standard", "budget_usd": 1000, "team_name": "alpha", "active": True}


def test_build_body_set_only() -> None:
    out = commands.build_body(None, ["scope=team", "scope_id=alpha"])
    assert json.loads(out) == {"scope": "team", "scope_id": "alpha"}


def test_build_body_bad_json_raises() -> None:
    with pytest.raises(GatewayError) as exc:
        commands.build_body("{not json", [])
    assert exc.value.code == "body_error"


def test_build_body_bad_set_token_raises() -> None:
    with pytest.raises(GatewayError) as exc:
        commands.build_body(None, ["no_equals_sign"])
    assert exc.value.code == "body_error"


def test_build_body_missing_file_raises() -> None:
    with pytest.raises(GatewayError) as exc:
        commands.build_body("@/nonexistent/path.json", [])
    assert exc.value.code == "body_error"


# ── CLI dispatch (stubbed client) ─────────────────────────────────────────────


class _StubClient:
    """Records the single request the command issues; returns a canned result."""

    def __init__(self, result=None, error: GatewayError | None = None) -> None:
        self.result = result if result is not None else {"ok": True}
        self.error = error
        self.calls: list[tuple] = []

    def __enter__(self) -> _StubClient:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def request(self, method, path, *, params=None, body=None):
        self.calls.append((method, path, params, body))
        if self.error is not None:
            raise self.error
        return self.result


def _install(monkeypatch, stub: _StubClient) -> None:
    monkeypatch.setattr(commands, "_client", lambda: stub)


def run_cli(tokens: list[str]) -> int:
    """Invoke the meta-app and return its process exit code.

    Cyclopts' default ``result_action`` turns the launcher's int return into a
    ``sys.exit(code)``, so the CLI raises ``SystemExit`` rather than returning.
    """
    try:
        commands.app.meta(tokens)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def test_teams_list_dispatches_get(monkeypatch, capsys) -> None:
    stub = _StubClient(result={"teams": [], "count": 0})
    _install(monkeypatch, stub)
    assert run_cli(["teams", "list"]) == 0
    assert stub.calls == [("GET", "/teams", None, None)]
    assert json.loads(capsys.readouterr().out) == {"teams": [], "count": 0}


def test_teams_get_positional(monkeypatch) -> None:
    stub = _StubClient()
    _install(monkeypatch, stub)
    assert run_cli(["teams", "get", "team-123"]) == 0
    assert stub.calls == [("GET", "/teams/team-123", None, None)]


def test_budgets_create_with_set(monkeypatch) -> None:
    stub = _StubClient()
    _install(monkeypatch, stub)
    assert run_cli(["budgets", "create", "--set", "scope=team", "--set", "budget_usd=1000"]) == 0
    method, path, _params, body = stub.calls[0]
    assert (method, path) == ("POST", "/budgets")
    assert json.loads(body) == {"scope": "team", "budget_usd": 1000}


def test_budgets_list_cursor_param(monkeypatch) -> None:
    stub = _StubClient()
    _install(monkeypatch, stub)
    assert run_cli(["budgets", "list", "--cursor", "abc"]) == 0
    assert stub.calls == [("GET", "/budgets", {"cursor": "abc"}, None)]


def test_pricing_upsert_two_positionals(monkeypatch) -> None:
    stub = _StubClient()
    _install(monkeypatch, stub)
    assert (
        run_cli(["pricing", "upsert", "bedrock", "claude-sonnet-4", "--set", "input_per_1k=0.003"])
        == 0
    )
    method, path, _params, body = stub.calls[0]
    assert (method, path) == ("PUT", "/pricing/bedrock/claude-sonnet-4")
    assert json.loads(body) == {"input_per_1k": 0.003}


def test_routing_delete(monkeypatch) -> None:
    stub = _StubClient()
    _install(monkeypatch, stub)
    assert run_cli(["routing", "delete", "prod-config"]) == 0
    assert stub.calls == [("DELETE", "/routing/configs/prod-config", None, None)]


def test_gateway_error_exits_nonzero_with_readable_message(monkeypatch, capsys) -> None:
    stub = _StubClient(error=GatewayError("not_found", "Team not found", status=404))
    _install(monkeypatch, stub)
    assert run_cli(["teams", "get", "missing"]) == 1
    err = capsys.readouterr().err
    assert "not_found" in err
    assert "Team not found" in err
    assert "Traceback" not in err  # readable, not a stack trace


def test_config_error_exits_nonzero(monkeypatch, capsys) -> None:
    """A missing-env config failure surfaces as a clean non-zero exit."""
    # Real _client() -> Config.from_env() with no gateway vars set.
    monkeypatch.delenv("GATEWAY_ADMIN_URL", raising=False)
    monkeypatch.delenv("GATEWAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("GATEWAY_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GATEWAY_TOKEN_ENDPOINT", raising=False)
    assert run_cli(["teams", "list"]) == 1
    err = capsys.readouterr().err
    assert "config_error" in err
    assert "Traceback" not in err


def test_raw_json_flag_emits_compact(monkeypatch, capsys) -> None:
    stub = _StubClient(result={"b": 2, "a": 1})
    _install(monkeypatch, stub)
    assert run_cli(["--json", "teams", "list"]) == 0
    out = capsys.readouterr().out.strip()
    # Compact (no spaces after separators) when --json is set.
    assert out in ('{"b":2,"a":1}', '{"a":1,"b":2}')
    assert "\n" not in out
