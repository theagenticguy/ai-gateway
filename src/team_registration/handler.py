"""Team registration Lambda — self-service onboarding API, on gwcore (ADR-016).

Authorization is enforced in-handler via gwcore (previously it was not —
``team_registration/auth.py`` was dead code, imported only by tests, so the
handler relied entirely on the API Gateway Cognito authorizer with no in-handler
defense in depth). Every request requires the admin scope; the legacy ``"admin"``
and canonical ``"https://gateway.internal/admin"`` strings are both accepted.

Routes:
- ``POST   /teams``              — Register a new team
- ``GET    /teams``              — List all active teams
- ``GET    /teams/{id}``         — Get team details + usage + budget
- ``POST   /teams/{id}/rotate``  — Rotate client credentials
- ``DELETE /teams/{id}``         — Deactivate team (revokes all tokens)
"""

from __future__ import annotations

import contextlib
import re
from typing import Any

from gwcore import audit, auth, errors, ok, responses
from gwcore.logging import bind, correlation_id, get_logger
from gwcore.telemetry import Timer, emit_metric
from team_registration.routes import (
    deactivate_team,
    get_team,
    list_teams,
    register_team,
    rotate_credentials,
)

logger = get_logger("team_registration")

_TEAM_ID_RE = re.compile(r"^/teams/([a-f0-9-]{36})$")
_ROTATE_RE = re.compile(r"^/teams/([a-f0-9-]{36})/rotate$")


def _method(event: dict[str, Any]) -> str:
    if m := event.get("httpMethod"):
        return str(m).upper()
    return str(event.get("requestContext", {}).get("http", {}).get("method", "GET")).upper()


def _path(event: dict[str, Any]) -> str:
    rc = event.get("requestContext", {})
    raw = rc.get("http", {}).get("path") or event.get("path") or event.get("rawPath", "/")
    return str(raw).rstrip("/") or "/teams"


def _route(method: str, path: str, event: dict[str, Any], principal: auth.Principal) -> dict[str, Any]:
    """Match method + path and dispatch. Routes raise typed gwcore errors."""
    if method == "POST" and path == "/teams":
        return register_team(event, principal)
    if method == "GET" and path == "/teams":
        return list_teams()
    if method == "GET" and (m := _TEAM_ID_RE.match(path)):
        return get_team(m.group(1))
    if method == "POST" and (m := _ROTATE_RE.match(path)):
        return rotate_credentials(m.group(1), event, principal)
    if method == "DELETE" and (m := _TEAM_ID_RE.match(path)):
        return deactivate_team(m.group(1), event, principal)
    raise errors.NotFoundError(f"Not found: {method} {path}")


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Lambda handler — authorizes, then routes to team registration endpoints."""
    cid = correlation_id(event)
    log = bind(logger, cid)
    method = _method(event)
    path = _path(event)

    if path == "/health" and method == "GET":
        return ok({"status": "healthy"})

    try:
        with Timer("RequestLatency", route="team_registration"):
            principal = auth.build_principal(event)
            auth.require(principal, scopes=[auth.ADMIN_SCOPE])
            log.info("admin request: %s %s by %s", method, path, principal.sub)
            return _route(method, path, event, principal)
    except errors.ControlPlaneError as exc:
        if exc.status in {401, 403}:
            log.info("team_registration authz rejected: %s %s (%s)", method, path, exc.code)
            emit_metric("AuthzDenied", 1, dimensions={"Route": "team_registration"})
            actor = "unknown"
            with contextlib.suppress(errors.ControlPlaneError):
                actor = auth.build_principal(event).sub or "unknown"
            audit.emit(
                audit.event_from_request(
                    event,
                    action="team.access",
                    actor=actor,
                    resource=f"{method} {path}",
                    decision="deny",
                    status=exc.status,
                    detail=exc.code,
                )
            )
        return responses.error_response(exc)
    except Exception:
        log.exception("Unhandled error in team_registration: %s %s", method, path)
        emit_metric("TeamRegistrationError", 1, dimensions={"Code": "internal_error"})
        return responses.error_response(errors.ControlPlaneError("Internal error"))
