"""Athena read path for the control-plane audit trail (ADR-016/017).

Backs ``GET /audit``. Runs a parameterized Athena query against the S3 Tables
Iceberg table ``control_plane.audit_events`` (columns = ``gwcore.audit``
``AuditEvent``) and maps the result rows to AuditEvent-shaped dicts.

The S3 Tables sub-catalog is addressed via the ``StartQueryExecution``
``QueryExecutionContext`` (``Catalog = s3tablescatalog/<bucket>``,
``Database = control_plane``) AND fully-qualified in the SQL, so the query
resolves regardless of workgroup default. The team filter and the ISO-8601
time bounds are bound as ``ExecutionParameters`` (``?`` placeholders) so the
caller-supplied ``team`` value can never be interpolated into the SQL text.

No live AWS is touched in tests: the Athena client is created lazily and
module-scoped (warm-Lambda reuse) and is patched in the unit tests.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any

import boto3

from gwcore import errors
from gwcore.logging import get_logger

logger = get_logger("budget_admin.audit_query")

# ── Configuration (set by the audit_query Terraform module on the Lambda) ──────
_WORKGROUP_ENV = "AUDIT_ATHENA_WORKGROUP"
_CATALOG_ENV = "AUDIT_ATHENA_CATALOG"  # s3tablescatalog/<bucket>
_DATABASE_ENV = "AUDIT_ATHENA_DATABASE"  # control_plane
_TABLE_BUCKET_ENV = "AUDIT_TABLE_BUCKET"  # the S3 Tables bucket name (for the FQ name)

_DEFAULT_DATABASE = "control_plane"
_DEFAULT_TABLE = "audit_events"

# Query polling bounds — Athena is async; poll GetQueryExecution to completion.
_POLL_INTERVAL_SECONDS = 0.5
_MAX_POLLS = 60  # ~30s ceiling; the Lambda itself times out at 30s

# Result-row cap safety net (also enforced via LIMIT in the SQL).
_MAX_LIMIT = 1000
_DEFAULT_LIMIT = 100

# Columns projected by the query (subset of AuditEvent; before/after omitted —
# they are heavy nested JSON and not needed for the governed read).
_COLUMNS = (
    "action",
    "actor",
    "resource",
    "decision",
    "status",
    "team",
    "source_ip",
    "correlation_id",
    "detail",
    "ts",
)
_INT_COLUMNS = frozenset({"status"})

_athena_client: Any = None


def _client() -> Any:
    """Lazily create and cache the Athena client (warm-Lambda reuse)."""
    global _athena_client  # noqa: PLW0603 — module-scoped client cache for warm Lambda
    if _athena_client is None:
        _athena_client = boto3.client("athena", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return _athena_client


def _table_fqn() -> str:
    """Fully-qualified S3 Tables name: "s3tablescatalog/<bucket>"."<db>"."<table>"."""
    catalog = os.environ.get(_CATALOG_ENV, "")
    database = os.environ.get(_DATABASE_ENV, _DEFAULT_DATABASE)
    if not catalog:
        bucket = os.environ.get(_TABLE_BUCKET_ENV, "")
        catalog = f"s3tablescatalog/{bucket}" if bucket else "s3tablescatalog"
    return f'"{catalog}"."{database}"."{_DEFAULT_TABLE}"'


def _validate_iso8601(value: str, field: str) -> str:
    """Validate an ISO-8601 timestamp string, raising a 400 on malformed input.

    Accepts a trailing ``Z`` (normalized to ``+00:00``) so callers can pass the
    common ``2026-06-01T00:00:00Z`` form. Returns the normalized value that
    Athena's ``from_iso8601_timestamp`` will parse.
    """
    candidate = value.strip()
    normalized = candidate[:-1] + "+00:00" if candidate.endswith("Z") else candidate
    try:
        datetime.fromisoformat(normalized)
    except (ValueError, TypeError) as exc:
        msg = f"Invalid ISO-8601 timestamp for '{field}'"
        raise errors.ValidationFailedError(msg, details={field: value}) from exc
    return normalized


def _coerce_limit(raw: str | int | None) -> int:
    """Parse + clamp the row limit to ``[1, _MAX_LIMIT]`` (default on bad input)."""
    if raw is None or raw == "":
        return _DEFAULT_LIMIT
    try:
        limit = int(raw)
    except (ValueError, TypeError):
        return _DEFAULT_LIMIT
    if limit < 1:
        return _DEFAULT_LIMIT
    return min(limit, _MAX_LIMIT)


def _build_query(limit: int) -> str:
    """Build the parameterized audit-by-team-period SQL.

    ``team`` and the two ISO-8601 bounds are ``?`` placeholders bound via
    ExecutionParameters. ``limit`` is validated to an int and inlined (LIMIT
    does not accept a bind parameter in Athena).
    """
    # S608 is a false positive here: no caller input is interpolated — team +
    # the two ISO-8601 bounds are bound as `?` ExecutionParameters, _table_fqn()
    # is built from server-controlled env vars only, and `limit` is a
    # validated/clamped int (LIMIT does not accept a bind parameter in Athena).
    return (
        "SELECT action, actor, resource, decision, status, team, source_ip, "  # noqa: S608
        "correlation_id, detail, ts "
        f"FROM {_table_fqn()} "
        "WHERE team = ? "
        "AND from_iso8601_timestamp(ts) "
        "BETWEEN from_iso8601_timestamp(?) AND from_iso8601_timestamp(?) "
        "ORDER BY from_iso8601_timestamp(ts) DESC "
        f"LIMIT {limit}"
    )


def _start_query(sql: str, params: list[str]) -> str:
    """Start the Athena query; return the QueryExecutionId."""
    workgroup = os.environ.get(_WORKGROUP_ENV, "")
    if not workgroup:
        msg = "Audit query surface is not configured (AUDIT_ATHENA_WORKGROUP unset)"
        raise errors.UpstreamError(msg, details={"config": "workgroup_missing"})

    catalog = os.environ.get(_CATALOG_ENV, "")
    database = os.environ.get(_DATABASE_ENV, _DEFAULT_DATABASE)
    context: dict[str, str] = {"Database": database}
    if catalog:
        context["Catalog"] = catalog

    resp = _client().start_query_execution(
        QueryString=sql,
        WorkGroup=workgroup,
        QueryExecutionContext=context,
        ExecutionParameters=params,
    )
    return str(resp["QueryExecutionId"])


def _await_completion(query_id: str) -> None:
    """Poll GetQueryExecution until the query reaches a terminal state.

    Raises ``UpstreamError`` on FAILED/CANCELLED or if it does not finish within
    the poll ceiling.
    """
    for _ in range(_MAX_POLLS):
        resp = _client().get_query_execution(QueryExecutionId=query_id)
        status = resp["QueryExecution"]["Status"]
        state = status.get("State", "")
        if state == "SUCCEEDED":
            return
        if state in {"FAILED", "CANCELLED"}:
            reason = status.get("StateChangeReason", "unknown")
            logger.warning("Athena audit query %s ended %s: %s", query_id, state, reason)
            raise errors.UpstreamError("Audit query failed", details={"state": state, "reason": reason})
        time.sleep(_POLL_INTERVAL_SECONDS)

    raise errors.UpstreamError("Audit query timed out", details={"query_id": query_id})


def _rows_to_records(result_set: dict[str, Any]) -> list[dict[str, Any]]:
    """Map an Athena ResultSet to AuditEvent-shaped dicts.

    The first row of a SELECT ResultSet is the column header; the rest are data.
    Each cell is ``{"VarCharValue": "..."}`` (or empty for NULL).
    """
    rows = result_set.get("Rows", [])
    if not rows:
        return []

    header = [c.get("VarCharValue", "") for c in rows[0].get("Data", [])]
    records: list[dict[str, Any]] = []
    for row in rows[1:]:
        cells = row.get("Data", [])
        raw: dict[str, str | None] = {}
        for idx, col in enumerate(header):
            value = cells[idx].get("VarCharValue") if idx < len(cells) else None
            raw[col] = value
        records.append(_normalize_record(raw))
    return records


def _normalize_record(raw: dict[str, str | None]) -> dict[str, Any]:
    """Project the known columns and coerce ``status`` to int."""
    record: dict[str, Any] = {}
    for col in _COLUMNS:
        value = raw.get(col)
        if col in _INT_COLUMNS:
            try:
                record[col] = int(value) if value not in (None, "") else None
            except (ValueError, TypeError):
                record[col] = None
        else:
            record[col] = value if value is not None else ""
    return record


def run_audit_query(*, team: str, start: str, end: str, limit: str | int | None = None) -> list[dict[str, Any]]:
    """Run the governed audit-by-team-period query and return AuditEvent dicts.

    Args:
        team: the team whose records to return (bound as an ExecutionParameter).
        start: inclusive ISO-8601 lower bound.
        end: inclusive ISO-8601 upper bound.
        limit: optional row cap (clamped to ``[1, 1000]``, default 100).

    Raises:
        ValidationFailedError: if ``start``/``end`` are not valid ISO-8601.
        UpstreamError: if the workgroup is unconfigured or the query fails.
    """
    start_norm = _validate_iso8601(start, "start")
    end_norm = _validate_iso8601(end, "end")
    row_limit = _coerce_limit(limit)

    sql = _build_query(row_limit)
    query_id = _start_query(sql, [team, start_norm, end_norm])
    _await_completion(query_id)
    result = _client().get_query_results(QueryExecutionId=query_id, MaxResults=min(row_limit + 1, 1000))
    return _rows_to_records(result.get("ResultSet", {}))
