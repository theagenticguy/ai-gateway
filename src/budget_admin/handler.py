"""Budget Admin API — Lambda Function URL handler.

Provides a REST API for managing budgets and querying usage data.
Parses the HTTP method and path from the Lambda Function URL event
and routes to the appropriate function.  Admin scope validation is
handled by the API Gateway Cognito authorizer.

Endpoints:
    GET    /budgets              — List all budgets (paginated)
    GET    /budgets/{id}         — Get budget + current usage
    POST   /budgets              — Create budget
    PUT    /budgets/{id}         — Update budget
    DELETE /budgets/{id}         — Delete budget
    GET    /usage/{scope}/{id}   — Get usage for team/user
    GET    /usage/{scope}/{id}/history — Daily usage breakdown
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

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

logger = logging.getLogger("budget_admin")
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


# ── Response helpers ─────────────────────────────────────────────────────────


def _json_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _error_response(status_code: int, message: str) -> dict[str, Any]:
    return _json_response(status_code, {"error": message})


# ── Request parsing ──────────────────────────────────────────────────────────


def _parse_body(event: dict[str, Any]) -> dict[str, Any]:
    """Parse the JSON body from a Lambda Function URL event."""
    body_str = event.get("body", "{}")
    if event.get("isBase64Encoded"):
        import base64  # noqa: PLC0415

        body_str = base64.b64decode(body_str).decode()
    if isinstance(body_str, str):
        return json.loads(body_str)  # type: ignore[no-any-return]
    return body_str  # type: ignore[return-value]


def _get_query_params(event: dict[str, Any]) -> dict[str, str]:
    """Extract query string parameters from the event."""
    return event.get("queryStringParameters") or {}


def _get_http_method(event: dict[str, Any]) -> str:
    """Extract the HTTP method from the event."""
    rc = event.get("requestContext", {})
    http = rc.get("http", {})
    return http.get("method", "GET").upper()


def _get_path(event: dict[str, Any]) -> str:
    """Extract the request path from the event."""
    rc = event.get("requestContext", {})
    http = rc.get("http", {})
    return http.get("path", event.get("rawPath", "/"))


# ── Request body parsing ─────────────────────────────────────────────────────


def _safe_parse_body(event: dict[str, Any]) -> dict[str, Any] | None:
    """Parse JSON body, returning None on failure."""
    try:
        return _parse_body(event)
    except (json.JSONDecodeError, Exception):
        return None


# ── Route dispatch ───────────────────────────────────────────────────────────


def _dispatch(method: str, path: str, event: dict[str, Any]) -> dict[str, Any]:  # noqa: PLR0911
    """Match method+path and dispatch to the appropriate route handler."""
    if method == "GET" and _RE_BUDGETS_LIST.match(path):
        return list_budgets(_get_query_params(event))

    if method == "POST" and _RE_BUDGETS_LIST.match(path):
        body = _safe_parse_body(event)
        if body is None:
            return _error_response(400, "Invalid JSON body")
        return create_budget(body)

    m = _RE_BUDGETS_DETAIL.match(path)
    if m:
        return _dispatch_budget_detail(method, m.group("budget_id"), event)

    m_hist = _RE_USAGE_HISTORY.match(path)
    if m_hist and method == "GET":
        return get_usage_history(
            m_hist.group("scope"),
            m_hist.group("scope_id"),
            _get_query_params(event),
        )

    m_usage = _RE_USAGE.match(path)
    if m_usage and method == "GET":
        return get_usage(m_usage.group("scope"), m_usage.group("scope_id"))

    return _error_response(404, f"Not found: {method} {path}")


def _dispatch_budget_detail(method: str, budget_id: str, event: dict[str, Any]) -> dict[str, Any]:
    """Dispatch budget detail endpoints (GET/PUT/DELETE on a single budget)."""
    if method == "GET":
        return get_budget(budget_id)

    if method == "PUT":
        body = _safe_parse_body(event)
        if body is None:
            return _error_response(400, "Invalid JSON body")
        return update_budget(budget_id, body)

    if method == "DELETE":
        return delete_budget(budget_id)

    return _error_response(404, f"Not found: {method} /budgets/{budget_id}")


# ── Lambda entry point ───────────────────────────────────────────────────────


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Lambda Function URL handler — routes requests to budget admin endpoints."""
    method = _get_http_method(event)
    path = _get_path(event)

    if path == "/health" and method == "GET":
        return _json_response(200, {"status": "healthy"})

    logger.info("Admin request: %s %s", method, path)

    return _dispatch(method, path, event)
