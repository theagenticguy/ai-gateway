"""Team registration Lambda (Function URL) — self-service API for onboarding.

Provides CRUD operations for team management:

- ``POST   /teams``              — Register a new team
- ``GET    /teams``              — List all active teams
- ``GET    /teams/{id}``         — Get team details + usage + budget
- ``POST   /teams/{id}/rotate``  — Rotate client credentials
- ``DELETE  /teams/{id}``        — Deactivate team (revokes all tokens)

Admin scope (``https://gateway.internal/admin``) is required for all operations.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from team_registration.auth import validate_admin_scope
from team_registration.routes import (
    deactivate_team,
    get_team,
    list_teams,
    register_team,
    rotate_credentials,
)

logger = logging.getLogger("team_registration")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(
        logging.Formatter(
            json.dumps(
                {
                    "timestamp": "%(asctime)s",
                    "level": "%(levelname)s",
                    "logger": "%(name)s",
                    "message": "%(message)s",
                }
            )
        )
    )
    logger.addHandler(_h)

# Route pattern: /teams/{team_id}
_TEAM_ID_RE = re.compile(r"^/teams/([a-f0-9-]{36})$")
# Route pattern: /teams/{team_id}/rotate
_ROTATE_RE = re.compile(r"^/teams/([a-f0-9-]{36})/rotate$")


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Lambda Function URL handler with route dispatch."""
    try:
        return _dispatch(event)
    except Exception:
        logger.exception("Unhandled error in team registration handler")
        return _response(500, {"error": "Internal server error"})


def _dispatch(event: dict[str, Any]) -> dict[str, Any]:
    """Parse the HTTP method + path and route to the right handler."""
    # Function URL puts HTTP info in requestContext.http
    request_ctx = event.get("requestContext", {})
    http = request_ctx.get("http", {})
    method = http.get("method", "").upper()
    path = http.get("path", "")

    # Normalize path: strip trailing slash, default to /teams
    path = path.rstrip("/") or "/teams"

    # Auth check
    auth_error = validate_admin_scope(event)
    if auth_error:
        return _response(403, {"error": auth_error})

    result, status = _route(method, path, event)
    return _response(status, result)


def _route(method: str, path: str, event: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Match method + path to a route handler and return (body, status)."""
    # POST /teams — register
    if method == "POST" and path == "/teams":
        return _handle_register(event)

    # GET /teams — list
    if method == "GET" and path == "/teams":
        return list_teams()

    # GET /teams/{id} — detail
    if method == "GET" and (m := _TEAM_ID_RE.match(path)):
        return get_team(m.group(1))

    # POST /teams/{id}/rotate — rotate credentials
    if method == "POST" and (m := _ROTATE_RE.match(path)):
        return rotate_credentials(m.group(1))

    # DELETE /teams/{id} — deactivate
    if method == "DELETE" and (m := _TEAM_ID_RE.match(path)):
        return deactivate_team(m.group(1))

    return {"error": f"Not found: {method} {path}"}, 404


def _handle_register(event: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Parse body and delegate to register_team."""
    body = _parse_body(event)
    if body is None:
        return {"error": "Invalid or missing JSON body"}, 400
    try:
        return register_team(body)
    except ValidationError as e:
        return {"error": f"Validation error: {e.error_count()} errors", "details": e.errors()}, 400


def _parse_body(event: dict[str, Any]) -> dict[str, Any] | None:
    """Extract JSON body from a Function URL event."""
    try:
        body_str = event.get("body", "")
        if event.get("isBase64Encoded"):
            import base64  # noqa: PLC0415

            body_str = base64.b64decode(body_str).decode()
        if not body_str:
            return None
        parsed = json.loads(body_str)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, Exception):
        return None


def _response(status_code: int, body: Any) -> dict[str, Any]:
    """Build a Lambda Function URL response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }
