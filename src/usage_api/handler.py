"""Real-time usage self-service API — migrated onto gwcore (ADR-016).

Read-only access to team usage data in DynamoDB (current period, trailing
history, per-model breakdown, budget utilization).

Tenant isolation is now enforced (it was not): a caller may read only their
OWN team's usage — ``principal.team`` from the token must match the requested
``team`` — unless they hold the admin scope. Previously any authenticated
caller could read any team's usage/spend via the ``team`` query param.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from gwcore import audit, auth, errors, ok, responses
from gwcore.logging import bind, correlation_id, get_logger
from gwcore.telemetry import Timer, emit_metric
from usage_api.models import ModelUsage, UsagePeriod, UsageResponse

logger = get_logger("usage_api")

USAGE_TABLE = os.environ.get("USAGE_TABLE", "gateway-usage")
BUDGETS_TABLE = os.environ.get("BUDGETS_TABLE", "gateway-budgets")

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))


# -- DynamoDB helpers ----------------------------------------------------------


def _get_team_usage(team: str, period: str) -> dict[str, Any] | None:
    """Fetch usage for a team in a single period from DynamoDB.

    Reads the real ``gateway-usage`` schema (issue #261): hash=``scope_id``
    (``team#<team>``), range=``period_date`` (``YYYY-MM``).
    """
    table = dynamodb.Table(USAGE_TABLE)
    resp = table.get_item(Key={"scope_id": f"team#{team}", "period_date": period})
    return resp.get("Item")


def _get_budget_config(team: str) -> dict[str, Any] | None:
    """Fetch the team-scoped budget configuration from DynamoDB (issue #261).

    The physical ``gateway-budgets`` table is keyed by ``budget_id`` (uuid) +
    ``scope``, so a lookup by team goes through the ``scope-index`` GSI
    (HASH=``scope`` == "CONFIG", RANGE=``scope_id`` == team), filtered to
    ``scope_type == "team"`` — the same path budget_enforcement uses.
    """
    from boto3.dynamodb.conditions import Attr  # noqa: PLC0415

    table = dynamodb.Table(BUDGETS_TABLE)
    resp = table.query(
        IndexName="scope-index",
        KeyConditionExpression=Key("scope").eq("CONFIG") & Key("scope_id").eq(team),
        FilterExpression=Attr("scope_type").eq("team"),
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def _get_model_usage_for_team(team: str, period: str) -> list[dict[str, Any]]:
    """Scan for all model-level usage rows for a team in a given period.

    DDB pattern (real ``gateway-usage`` schema, issue #261): ``scope_id``
    begins_with ``team#{team}#model#``, ``period_date`` == ``{period}``.
    """
    table = dynamodb.Table(USAGE_TABLE)
    items: list[dict[str, Any]] = []

    filter_expr = Key("scope_id").begins_with(f"team#{team}#model#") & Key("period_date").eq(period)
    response = table.scan(FilterExpression=filter_expr)
    items.extend(response.get("Items", []))

    while "LastEvaluatedKey" in response:
        response = table.scan(
            FilterExpression=filter_expr,
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))

    return items


# -- Period helpers ------------------------------------------------------------


def _current_period() -> str:
    """Return the current billing period as YYYY-MM."""
    return datetime.now(tz=UTC).strftime("%Y-%m")


def _trailing_periods(n: int) -> list[str]:
    """Return the last *n* billing periods (most recent first), including the current one."""
    now = datetime.now(tz=UTC)
    periods: list[str] = []
    year, month = now.year, now.month
    for _ in range(n):
        periods.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return periods


# -- Item-to-model helpers -----------------------------------------------------


def _item_to_usage_period(item: dict[str, Any], period: str) -> UsagePeriod:
    """Convert a DynamoDB item to a ``UsagePeriod`` model."""
    return UsagePeriod(
        period=period,
        total_tokens=int(item.get("total_tokens", 0)),
        input_tokens=int(item.get("input_tokens", 0)),
        output_tokens=int(item.get("output_tokens", 0)),
        cached_tokens=int(item.get("cached_tokens", 0)),
        total_cost_usd=_safe_decimal(item.get("total_cost_usd", "0")),
        request_count=int(item.get("request_count", 0)),
    )


def _item_to_model_usage(item: dict[str, Any]) -> ModelUsage | None:
    """Convert a DynamoDB model-level usage item to a ``ModelUsage`` model.

    Extracts the model name from the scope_id: ``team#{team}#model#{model}``
    (issue #261, real ``gateway-usage`` schema).
    """
    scope_id = item.get("scope_id", "")
    marker = "#model#"
    idx = scope_id.find(marker)
    if idx == -1:
        return None
    model_name = scope_id[idx + len(marker) :]
    if not model_name:
        return None

    return ModelUsage(
        model=model_name,
        total_tokens=int(item.get("total_tokens", 0)),
        input_tokens=int(item.get("input_tokens", 0)),
        output_tokens=int(item.get("output_tokens", 0)),
        total_cost_usd=_safe_decimal(item.get("total_cost_usd", "0")),
        request_count=int(item.get("request_count", 0)),
    )


def _safe_decimal(value: Any) -> Decimal:
    """Safely convert a value to Decimal, falling back to 0.00."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


# -- Route handler -------------------------------------------------------------


def _handle_usage(team: str, history: int, models: bool) -> dict[str, Any]:
    """Core handler logic: fetch usage data and build the response."""
    period = _current_period()

    try:
        current_item = _get_team_usage(team, period)
    except ClientError as e:
        raise errors.UpstreamError("Failed to query usage data") from e

    current_period = _item_to_usage_period(current_item, period) if current_item else None

    budget_utilization_pct: float | None = None
    monthly_budget_usd: Decimal | None = None
    try:
        budget_item = _get_budget_config(team)
        if budget_item:
            # budget_admin writes the cap as ``budget_usd`` (issue #261); fall
            # back to the legacy ``monthly_budget_usd`` name for older records.
            monthly_budget_usd = _safe_decimal(
                budget_item.get("budget_usd", budget_item.get("monthly_budget_usd", "0"))
            )
            if monthly_budget_usd > 0 and current_period:
                budget_utilization_pct = round(float(current_period.total_cost_usd / monthly_budget_usd * 100), 1)
    except ClientError:
        # Non-fatal: still return usage data without budget info.
        logger.exception("Failed to query budget config for team=%s", team)

    usage_history: list[UsagePeriod] = []
    if history > 0:
        for p in _trailing_periods(history):
            try:
                item = _get_team_usage(team, p)
            except ClientError as e:
                raise errors.UpstreamError("Failed to query usage data") from e
            if item:
                usage_history.append(_item_to_usage_period(item, p))

    model_list: list[ModelUsage] = []
    if models:
        try:
            model_items = _get_model_usage_for_team(team, period)
        except ClientError as e:
            raise errors.UpstreamError("Failed to query usage data") from e
        model_list = [mu for item in model_items if (mu := _item_to_model_usage(item))]
        model_list.sort(key=lambda m: m.total_cost_usd, reverse=True)

    response = UsageResponse(
        team=team,
        current_period=current_period,
        history=usage_history,
        models=model_list,
        budget_utilization_pct=budget_utilization_pct,
        monthly_budget_usd=monthly_budget_usd,
    )
    return ok(response.model_dump(mode="json"))


# -- Lambda entry point --------------------------------------------------------


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Lambda handler for the usage self-service API.

    Routes:
        GET /usage?team=X              -- current period usage for team X
        GET /usage?team=X&history=N    -- trailing N months of usage history
        GET /usage?team=X&models=true  -- per-model breakdown for current period
    """
    cid = correlation_id(event)
    log = bind(logger, cid)

    method = (event.get("requestContext", {}).get("http", {}).get("method") or event.get("httpMethod") or "GET").upper()
    path = event.get("rawPath") or event.get("path") or ""
    if path == "/health" and method == "GET":
        return ok({"status": "healthy"})

    try:
        with Timer("RequestLatency", route="usage_api"):
            principal = auth.build_principal(event)
            auth.require(principal, scopes=[auth.INVOKE_SCOPE])

            params = event.get("queryStringParameters") or {}
            team = params.get("team", "")
            if not team:
                msg = "Missing required parameter: team"
                raise errors.ValidationFailedError(msg)  # noqa: TRY301 — direct request guard

            # Tenant isolation: a non-admin may read only their OWN team's usage.
            # A non-admin whose token carries a different (or empty) team claim is
            # denied — an empty claim must not bypass the check, or it would grant
            # cross-team reads via the ?team= param.
            if not principal.is_admin and principal.team != team:
                msg = "Cannot read usage for another team"
                raise errors.ForbiddenError(  # noqa: TRY301 — direct tenant-isolation guard
                    msg, details={"requested": team, "your_team": principal.team}
                )

            try:
                history = int(params.get("history", "0"))
            except (ValueError, TypeError):
                history = 0
            models = params.get("models", "").lower() == "true"

            log.info("usage request: team=%s history=%d models=%s by=%s", team, history, models, principal.sub)
            return _handle_usage(team, history, models)
    except errors.ControlPlaneError as exc:
        if exc.status in {401, 403}:
            emit_metric("AuthzDenied", 1, dimensions={"Route": "usage_api"})
            try:
                actor = auth.build_principal(event).sub or "unknown"
            except errors.ControlPlaneError:
                actor = "unknown"
            audit.emit(
                audit.event_from_request(
                    event,
                    action="usage.access",
                    actor=actor,
                    resource=f"{method} {path or '/usage'}",
                    decision="deny",
                    status=exc.status,
                    detail=exc.code,
                )
            )
        return responses.error_response(exc)
    except Exception:
        log.exception("Unhandled error in usage_api")
        emit_metric("UsageApiError", 1, dimensions={"Code": "internal_error"})
        return responses.error_response(errors.ControlPlaneError("Internal error"))
