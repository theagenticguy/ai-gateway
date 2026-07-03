"""Cyclopts command tree for the admin CLI.

Maps 1:1 onto the control-plane admin API. Every command forwards a JSON body
(built from ``--body`` and/or ``--set k=v`` flags) and prints the parsed
response. Body shaping is intentionally NOT validated here — the server owns the
Pydantic models; the CLI just forwards JSON.

Each command function has a unique Python name and is registered under its CLI
verb via ``@group.command(name=...)`` (so ``teams_list`` becomes ``teams list``,
etc.). This keeps the module free of name collisions while giving natural verbs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any

from cyclopts import App, Parameter

from admin_cli import __version__
from admin_cli.client import Config, GatewayClient, GatewayError

# Global output mode, toggled by the meta-app ``--json`` flag. Default is pretty.
_RAW_JSON = False

# Shared body flags, reused across every mutating command.
BodyOpt = Annotated[
    str | None,
    Parameter(
        name=("--body", "-b"),
        help="JSON body: inline string, or @path/to/file.json to read from a file.",
    ),
]
SetOpt = Annotated[
    list[str] | None,
    Parameter(
        name=("--set", "-s"),
        help=(
            "Set a top-level body field: --set key=value (repeatable). "
            "JSON-typed when the value parses as JSON, else treated as a string."
        ),
    ),
]
CursorOpt = Annotated[
    str | None,
    Parameter(help="Opaque pagination cursor from a prior list's next_cursor."),
]


app = App(
    name="admin-cli",
    version=__version__,
    help="Thin admin client for the AI Gateway control plane (Cognito M2M auth).",
)

teams = App(name="teams", help="Team registration: create, list, get, rotate, delete.")
budgets = App(name="budgets", help="Budgets: list (paginated), create, get, update, delete.")
routing = App(name="routing", help="Routing configs: list, get, create, update, delete.")
pricing = App(name="pricing", help="Pricing overrides: list, get, upsert, delete.")
app.command(teams)
app.command(budgets)
app.command(routing)
app.command(pricing)


# ── body + output helpers ─────────────────────────────────────────────────────


def _coerce_value(value: str) -> Any:
    """Parse a ``--set`` value as JSON, falling back to the raw string."""
    try:
        return json.loads(value)
    except ValueError:
        return value


def build_body(body: str | None, sets: list[str]) -> str | None:
    """Build a JSON body string from ``--body`` and ``--set key=value`` flags.

    ``--body`` may be inline JSON or ``@file.json``. ``--set`` overlays scalar
    fields on top. Returns ``None`` when neither is given (e.g. a GET).

    Raises:
        GatewayError: on malformed JSON, a missing @file, or a bad --set token.
    """
    payload: Any = {}
    if body is not None:
        text = body
        if body.startswith("@"):
            path = Path(body[1:]).expanduser()
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise GatewayError("body_error", f"Cannot read body file: {exc}") from exc
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise GatewayError("body_error", f"Invalid JSON in --body: {exc}") from exc

    if sets:
        if not isinstance(payload, dict):
            raise GatewayError("body_error", "--set requires the base body to be a JSON object")
        for item in sets:
            if "=" not in item:
                raise GatewayError("body_error", f"--set expects key=value, got: {item!r}")
            key, raw = item.split("=", 1)
            payload[key.strip()] = _coerce_value(raw)

    if body is None and not sets:
        return None
    return json.dumps(payload)


def _print(result: Any) -> None:
    """Print a parsed response as pretty (default) or raw JSON."""
    if result is None:
        return
    if _RAW_JSON:
        sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
    else:
        sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")


def _client() -> GatewayClient:
    """Build a client from resolved env/flag configuration."""
    return GatewayClient(Config.from_env())


def _run(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: str | None = None,
) -> None:
    """Execute one admin-API call and print the result (shared command body)."""
    with _client() as client:
        _print(client.request(method, path, params=params, body=body))


# ── teams ───────────────────────────────────────────────────────────────────


@teams.command(name="list")
def teams_list() -> None:
    """GET /teams — list all registered teams."""
    _run("GET", "/teams")


@teams.command(name="get")
def teams_get(team_id: str, /) -> None:
    """GET /teams/{id} — fetch one team."""
    _run("GET", f"/teams/{team_id}")


@teams.command(name="create")
def teams_create(*, body: BodyOpt = None, set: SetOpt = None) -> None:
    """POST /teams — register a team (body: team_name, contact_email, tier, description)."""
    _run("POST", "/teams", body=build_body(body, set or []))


@teams.command(name="rotate")
def teams_rotate(team_id: str, /) -> None:
    """POST /teams/{id}/rotate — rotate a team's client credentials."""
    _run("POST", f"/teams/{team_id}/rotate")


@teams.command(name="delete")
def teams_delete(team_id: str, /) -> None:
    """DELETE /teams/{id} — deactivate a team (deletes its Cognito client)."""
    _run("DELETE", f"/teams/{team_id}")


# ── budgets ─────────────────────────────────────────────────────────────────


@budgets.command(name="list")
def budgets_list(*, cursor: CursorOpt = None) -> None:
    """GET /budgets — list budgets (cursor-paginated; follow next_cursor)."""
    _run("GET", "/budgets", params={"cursor": cursor})


@budgets.command(name="get")
def budgets_get(budget_id: str, /) -> None:
    """GET /budgets/{id} — fetch one budget."""
    _run("GET", f"/budgets/{budget_id}")


@budgets.command(name="create")
def budgets_create(*, body: BodyOpt = None, set: SetOpt = None) -> None:
    """POST /budgets — create a budget (body: scope, scope_id, budget_usd, ...)."""
    _run("POST", "/budgets", body=build_body(body, set or []))


@budgets.command(name="update")
def budgets_update(budget_id: str, /, *, body: BodyOpt = None, set: SetOpt = None) -> None:
    """PUT /budgets/{id} — partial update (body: budget_usd, period, ...)."""
    _run("PUT", f"/budgets/{budget_id}", body=build_body(body, set or []))


@budgets.command(name="delete")
def budgets_delete(budget_id: str, /) -> None:
    """DELETE /budgets/{id} — delete a budget."""
    _run("DELETE", f"/budgets/{budget_id}")


# ── routing ─────────────────────────────────────────────────────────────────


@routing.command(name="list")
def routing_list() -> None:
    """GET /routing/configs — list all routing configs."""
    _run("GET", "/routing/configs")


@routing.command(name="get")
def routing_get(name: str, /) -> None:
    """GET /routing/configs/{name} — fetch one routing config."""
    _run("GET", f"/routing/configs/{name}")


@routing.command(name="create")
def routing_create(*, body: BodyOpt = None, set: SetOpt = None) -> None:
    """POST /routing/configs — create a config (body: strategy, targets, metadata)."""
    _run("POST", "/routing/configs", body=build_body(body, set or []))


@routing.command(name="update")
def routing_update(name: str, /, *, body: BodyOpt = None, set: SetOpt = None) -> None:
    """PUT /routing/configs/{name} — update a routing config."""
    _run("PUT", f"/routing/configs/{name}", body=build_body(body, set or []))


@routing.command(name="delete")
def routing_delete(name: str, /) -> None:
    """DELETE /routing/configs/{name} — delete a routing config."""
    _run("DELETE", f"/routing/configs/{name}")


# ── pricing ─────────────────────────────────────────────────────────────────


@pricing.command(name="list")
def pricing_list() -> None:
    """GET /pricing — list all prices (DynamoDB overrides + static merged)."""
    _run("GET", "/pricing")


@pricing.command(name="get")
def pricing_get(provider: str, model: str, /) -> None:
    """GET /pricing/{provider}/{model} — fetch one price entry."""
    _run("GET", f"/pricing/{provider}/{model}")


@pricing.command(name="upsert")
def pricing_upsert(
    provider: str, model: str, /, *, body: BodyOpt = None, set: SetOpt = None
) -> None:
    """PUT /pricing/{provider}/{model} — upsert a pricing entry (body: input_per_1k, ...)."""
    _run("PUT", f"/pricing/{provider}/{model}", body=build_body(body, set or []))


@pricing.command(name="delete")
def pricing_delete(provider: str, model: str, /) -> None:
    """DELETE /pricing/{provider}/{model} — delete a pricing override."""
    _run("DELETE", f"/pricing/{provider}/{model}")


# ── meta-app: global --json flag + clean error handling ───────────────────────


@app.meta.default
def _launcher(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    json: Annotated[
        bool,
        Parameter(name="--json", help="Emit raw compact JSON instead of pretty-printed output."),
    ] = False,
) -> int:
    """Dispatch to the command tree, applying the global --json flag.

    A :class:`GatewayError` (config, auth, or an API error envelope) is printed
    as a readable one-liner on stderr and the process exits non-zero — never a
    stack trace.
    """
    global _RAW_JSON
    _RAW_JSON = json
    try:
        app(tokens)
    except GatewayError as exc:
        detail = f" ({exc.details})" if exc.details else ""
        sys.stderr.write(f"error: {exc.code}: {exc.message}{detail}\n")
        return 1
    return 0


def main() -> None:
    """Console-script / module entry point.

    ``app.meta()`` dispatches and, per cyclopts' default ``result_action``,
    calls ``sys.exit(code)`` with the launcher's return code (0 on success,
    1 on a :class:`GatewayError`). It therefore raises ``SystemExit`` rather
    than returning — callers should not wrap it in another ``sys.exit``.
    """
    app.meta()
