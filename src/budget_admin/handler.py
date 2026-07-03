"""Budget Admin API — Lambda handler, migrated onto gwcore (ADR-016).

Authorization is now enforced here via gwcore (it previously was not —
``budget_admin/auth.py`` was dead code, imported only by tests, so the handler
relied entirely on the API Gateway Cognito authorizer with no in-handler
defense in depth). Every request now builds a ``Principal`` and requires the
admin scope; the legacy ``"admin"`` and canonical ``"https://gateway.internal/
admin"`` strings are both accepted (the scope-divergence bug-fix).

Endpoints:
    GET    /budgets              — List all budgets (cursor-paginated)
    GET    /budgets/{id}         — Get budget + current usage
    POST   /budgets              — Create budget
    PUT    /budgets/{id}         — Update budget
    DELETE /budgets/{id}         — Delete budget
    GET    /usage/{scope}/{id}   — Get usage for team/user
    GET    /usage/{scope}/{id}/history — Daily usage breakdown
"""

from __future__ import annotations

import contextlib
import os
import re
from typing import Any

from budget_admin.audit_query import run_audit_query
from budget_admin.routes import (
    create_budget,
    delete_budget,
    get_budget,
    get_usage,
    get_usage_history,
    init_dynamodb,
    list_budgets,
    update_budget,
)
from gwcore import audit, auth, errors, ok, page, responses
from gwcore.logging import bind, correlation_id, get_logger
from gwcore.telemetry import Timer, emit_metric

logger = get_logger("budget_admin")

# Initialize DynamoDB on cold start
init_dynamodb(
    budgets_table=os.environ.get("BUDGETS_TABLE", "gateway-budgets"),
    usage_table=os.environ.get("USAGE_TABLE", "gateway-usage"),
    region=os.environ.get("AWS_REGION", "us-east-1"),
)

# ── Path patterns ────────────────────────────────────────────────────────────

_RE_BUDGETS_LIST = re.compile(r"^/budgets/?$")
_RE_BUDGETS_DETAIL = re.compile(r"^/budgets/(?P<budget_id>[^/]+)/?$")
_RE_USAGE = re.compile(r"^/usage/(?P<scope>[^/]+)/(?P<scope_id>[^/]+)/?$")
_RE_USAGE_HISTORY = re.compile(r"^/usage/(?P<scope>[^/]+)/(?P<scope_id>[^/]+)/history/?$")
_RE_AUDIT = re.compile(r"^/audit/?$")


# ── Request parsing ──────────────────────────────────────────────────────────


def _get_http_method(event: dict[str, Any]) -> str:
    """Extract the HTTP method (REST proxy ``httpMethod`` or v2 ``http.method``)."""
    if method := event.get("httpMethod"):
        return str(method).upper()
    rc = event.get("requestContext", {})
    return str(rc.get("http", {}).get("method", "GET")).upper()


def _get_path(event: dict[str, Any]) -> str:
    """Extract the request path across REST proxy / Function URL event shapes."""
    rc = event.get("requestContext", {})
    http = rc.get("http", {})
    return str(http.get("path") or event.get("path") or event.get("rawPath", "/"))


def _query_params(event: dict[str, Any]) -> dict[str, str]:
    return event.get("queryStringParameters") or {}


# ── Route dispatch ───────────────────────────────────────────────────────────


def _dispatch(method: str, path: str, event: dict[str, Any], principal: auth.Principal) -> dict[str, Any]:
    """Match method+path and dispatch to the appropriate route handler."""
    if method == "GET" and _RE_AUDIT.match(path):
        return _get_audit(_query_params(event), principal)

    if method == "GET" and _RE_BUDGETS_LIST.match(path):
        return list_budgets(_query_params(event))

    if method == "POST" and _RE_BUDGETS_LIST.match(path):
        return create_budget(event, principal)

    if m := _RE_BUDGETS_DETAIL.match(path):
        return _dispatch_budget_detail(method, m.group("budget_id"), event, principal)

    if (m_hist := _RE_USAGE_HISTORY.match(path)) and method == "GET":
        return get_usage_history(m_hist.group("scope"), m_hist.group("scope_id"), _query_params(event))

    if (m_usage := _RE_USAGE.match(path)) and method == "GET":
        return get_usage(m_usage.group("scope"), m_usage.group("scope_id"))

    raise errors.NotFoundError(f"Not found: {method} {path}")


def _dispatch_budget_detail(
    method: str, budget_id: str, event: dict[str, Any], principal: auth.Principal
) -> dict[str, Any]:
    """Dispatch single-budget endpoints (GET/PUT/DELETE)."""
    if method == "GET":
        return get_budget(budget_id)
    if method == "PUT":
        return update_budget(budget_id, event, principal)
    if method == "DELETE":
        return delete_budget(budget_id, event, principal)
    raise errors.NotFoundError(f"Not found: {method} /budgets/{budget_id}")


# ── Audit read (GET /audit) ────────────────────────────────────────────────────


def _get_audit(params: dict[str, str], principal: auth.Principal) -> dict[str, Any]:
    """Governed read of the control-plane audit trail for a team over a period.

    Route: ``GET /audit?team=<t>&start=<iso>&end=<iso>&limit=<n>``. The Lambda
    entry point has already required the admin scope, so admins may read any
    team. The ADR-008 team-isolation guard is kept regardless: if the scope is
    ever relaxed to INVOKE, a non-admin may read only their OWN team, and an
    empty team claim must NOT bypass the check.

    Reads are NOT mutations, so this route emits no audit event (only mutations
    and authz denials are audited).
    """
    team = params.get("team", "")
    if not team:
        msg = "Missing required parameter: team"
        raise errors.ValidationFailedError(msg)

    # ADR-008 tenant isolation (verbatim in spirit from usage_api): a non-admin
    # may read only their OWN team's audit trail; an empty/mismatched team claim
    # is denied so it cannot grant a cross-team read via the ?team= param.
    if not principal.is_admin and principal.team != team:
        msg = "Cannot read the audit trail for another team"
        raise errors.ForbiddenError(msg, details={"requested": team, "your_team": principal.team})

    start = params.get("start", "")
    end = params.get("end", "")
    if not start or not end:
        msg = "Missing required parameters: start and end (ISO-8601)"
        raise errors.ValidationFailedError(msg)

    # run_audit_query validates the ISO-8601 bounds (400 on bad input) and clamps
    # the limit. The team value is bound as an Athena ExecutionParameter, never
    # interpolated into the SQL text.
    items = run_audit_query(team=team, start=start, end=end, limit=params.get("limit"))
    return page(items)


# ── Lambda entry point ───────────────────────────────────────────────────────


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Lambda handler — authorizes, then routes to budget admin endpoints."""
    cid = correlation_id(event)
    log = bind(logger, cid)
    method = _get_http_method(event)
    path = _get_path(event)

    if path == "/health" and method == "GET":
        return ok({"status": "healthy"})

    try:
        with Timer("RequestLatency", route="budget_admin"):
            # AuthN + AuthZ: every non-health request requires the admin scope.
            principal = auth.build_principal(event)
            auth.require(principal, scopes=[auth.ADMIN_SCOPE])
            log.info("admin request: %s %s by %s", method, path, principal.sub)
            return _dispatch(method, path, event, principal)
    except errors.ControlPlaneError as exc:
        if exc.status in {401, 403}:
            log.info("budget_admin authz rejected: %s %s (%s)", method, path, exc.code)
            emit_metric("AuthzDenied", 1, dimensions={"Route": "budget_admin"})
            # Audit the denial too (ADR-016: record allow AND deny). Actor is
            # the token sub when derivable, else "unknown" for a bad/absent token.
            actor = "unknown"
            with contextlib.suppress(errors.ControlPlaneError):
                actor = auth.build_principal(event).sub or "unknown"
            audit.emit(
                audit.event_from_request(
                    event,
                    action="budget.access",
                    actor=actor,
                    resource=f"{method} {path}",
                    decision="deny",
                    status=exc.status,
                    detail=exc.code,
                )
            )
        return responses.error_response(exc)
    except Exception:
        log.exception("Unhandled error in budget_admin: %s %s", method, path)
        emit_metric("BudgetAdminError", 1, dimensions={"Code": "internal_error"})
        return responses.error_response(errors.ControlPlaneError("Internal error"))
