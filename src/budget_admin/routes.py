"""Route implementations for the Budget Admin API (migrated onto gwcore).

Each function corresponds to a REST endpoint and interacts with DynamoDB
budget and usage tables. Responses use the gwcore envelope; failures raise
typed ``gwcore.errors`` (mapped to HTTP by the handler); list uses gwcore
cursor pagination; mutations emit a ``gwcore.audit`` event.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import boto3
from botocore.exceptions import ClientError
from pydantic import ValidationError

from budget_admin.models import (
    BudgetResponse,
    CreateBudgetRequest,
    UpdateBudgetRequest,
    UsageResponse,
)
from gwcore import audit, auth, errors, ok, page, parse_cursor
from gwcore.responses import request_body

logger = logging.getLogger("budget_admin.routes")

_PAGE_LIMIT = 25


# ── Helpers ──────────────────────────────────────────────────────────────────


def _client_error_code(e: ClientError) -> str:
    """Safely extract the error code from a ClientError response."""
    return e.response.get("Error", {}).get("Code", "Unknown")  # type: ignore[union-attr]


def _upstream(e: ClientError, action: str) -> errors.UpstreamError:
    """Map a DynamoDB ClientError to a typed UpstreamError."""
    logger.exception("DynamoDB error during %s", action)
    return errors.UpstreamError("DynamoDB error", details={"code": _client_error_code(e)})


# Table names from environment (set in handler.py at init time)
_budgets_table_name: str = ""
_usage_table_name: str = ""
_dynamodb: Any = None


def init_dynamodb(budgets_table: str, usage_table: str, region: str = "us-east-1") -> None:
    """Initialize the DynamoDB resource and table names (called at cold start)."""
    global _budgets_table_name, _usage_table_name, _dynamodb  # noqa: PLW0603
    _budgets_table_name = budgets_table
    _usage_table_name = usage_table
    _dynamodb = boto3.resource("dynamodb", region_name=region)


def _budgets_table() -> Any:
    return _dynamodb.Table(_budgets_table_name)


def _usage_table() -> Any:
    return _dynamodb.Table(_usage_table_name)


def _audit(event: dict[str, Any], principal: auth.Principal, **kw: Any) -> None:
    """Emit a control-plane audit event for a budget mutation."""
    audit.emit(audit.event_from_request(event, actor=principal.sub, team=principal.team, **kw))


# ── Budget CRUD ──────────────────────────────────────────────────────────────


def list_budgets(query_params: dict[str, str] | None = None) -> dict[str, Any]:
    """List budgets with gwcore cursor pagination (DynamoDB Scan)."""
    params: dict[str, Any] = {"Limit": _PAGE_LIMIT}
    cursor = (query_params or {}).get("cursor")
    start_key = parse_cursor(cursor)  # raises ValidationFailedError on malformed cursor
    if start_key:
        params["ExclusiveStartKey"] = start_key

    try:
        resp = _budgets_table().scan(**params)
    except ClientError as e:
        raise _upstream(e, "list_budgets") from e

    return page(resp.get("Items", []), resp.get("LastEvaluatedKey"))


def get_budget(budget_id: str) -> dict[str, Any]:
    """Get a single budget by ID, including current-period usage."""
    try:
        resp = _budgets_table().get_item(Key={"budget_id": budget_id, "scope": "CONFIG"})
    except ClientError as e:
        raise _upstream(e, "get_budget") from e

    item = resp.get("Item")
    if not item:
        raise errors.NotFoundError(f"Budget {budget_id} not found")

    current_usage_usd = Decimal("0.00")
    current_tokens = 0
    scope_id = item.get("scope_id", "")
    if scope_id:
        period = datetime.now(tz=UTC).strftime("%Y-%m")
        try:
            usage_resp = _usage_table().get_item(Key={"scope_id": scope_id, "period_date": period})
            usage_item = usage_resp.get("Item")
            if usage_item:
                try:
                    current_usage_usd = Decimal(str(usage_item.get("total_cost_usd", "0")))
                except (InvalidOperation, TypeError, ValueError):
                    current_usage_usd = Decimal("0.00")
                current_tokens = int(usage_item.get("total_tokens", 0))
        except ClientError:
            logger.warning("Failed to fetch usage for scope_id=%s, returning budget without usage", scope_id)

    budget_resp = BudgetResponse(
        budget_id=item["budget_id"],
        scope=item.get("scope_type", item.get("scope", "")),
        scope_id=scope_id,
        budget_usd=Decimal(str(item.get("budget_usd", "0"))),
        token_limit=item.get("token_limit"),
        period=item.get("period", "monthly"),
        tier=item.get("tier", "standard"),
        model_limits=item.get("model_limits", []),
        alert_thresholds=item.get("alert_thresholds", []),
        current_usage_usd=current_usage_usd,
        current_tokens=current_tokens,
        created_at=item.get("created_at"),
        updated_at=item.get("updated_at"),
    )
    return ok(budget_resp.model_dump(exclude_none=True, mode="json"))


def create_budget(event: dict[str, Any], principal: auth.Principal) -> dict[str, Any]:
    """Create a new budget. Fails if a budget with the same ID already exists."""
    raw_body = request_body(event)
    try:
        request = CreateBudgetRequest.model_validate_json(raw_body)
    except ValidationError as e:
        raise errors.ValidationFailedError("Invalid budget", details={"errors": e.errors()}) from e

    budget_id = str(uuid.uuid4())
    now_iso = datetime.now(tz=UTC).isoformat()

    item: dict[str, Any] = {
        "budget_id": budget_id,
        "scope": "CONFIG",  # sort key
        "scope_type": request.scope.value,
        "scope_id": request.scope_id,
        "budget_usd": request.budget_usd,
        "period": request.period.value,
        "tier": request.tier.value,
        "alert_thresholds": request.alert_thresholds,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    if request.token_limit is not None:
        item["token_limit"] = request.token_limit
    if request.model_limits:
        item["model_limits"] = [ml.model_dump(mode="json") for ml in request.model_limits]

    try:
        _budgets_table().put_item(Item=item, ConditionExpression="attribute_not_exists(budget_id)")
    except ClientError as e:
        if _client_error_code(e) == "ConditionalCheckFailedException":
            raise errors.ConflictError(f"Budget {budget_id} already exists") from e
        raise _upstream(e, "create_budget") from e

    _audit(
        event,
        principal,
        action="budget.create",
        resource=budget_id,
        after={"scope": request.scope.value, "scope_id": request.scope_id, "budget_usd": str(request.budget_usd)},
        status=201,
    )
    return ok({"budget_id": budget_id, "message": "Budget created"}, status=201)


def update_budget(budget_id: str, event: dict[str, Any], principal: auth.Principal) -> dict[str, Any]:
    """Update a budget with partial fields via UpdateItem expression builder."""
    raw_body = request_body(event)
    try:
        request = UpdateBudgetRequest.model_validate_json(raw_body)
    except ValidationError as e:
        raise errors.ValidationFailedError("Invalid budget update", details={"errors": e.errors()}) from e

    fields = request.model_dump(exclude_none=True, mode="json")
    if not fields:
        raise errors.ValidationFailedError("No fields to update")

    update_parts: list[str] = []
    expr_names: dict[str, str] = {}
    expr_values: dict[str, Any] = {}
    for field_name, value in fields.items():
        update_parts.append(f"#{field_name} = :{field_name}")
        expr_names[f"#{field_name}"] = field_name
        expr_values[f":{field_name}"] = value
    update_parts.append("#updated_at = :updated_at")
    expr_names["#updated_at"] = "updated_at"
    expr_values[":updated_at"] = datetime.now(tz=UTC).isoformat()

    try:
        resp = _budgets_table().update_item(
            Key={"budget_id": budget_id, "scope": "CONFIG"},
            UpdateExpression="SET " + ", ".join(update_parts),
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ConditionExpression="attribute_exists(budget_id)",
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if _client_error_code(e) == "ConditionalCheckFailedException":
            raise errors.NotFoundError(f"Budget {budget_id} not found") from e
        raise _upstream(e, "update_budget") from e

    _audit(event, principal, action="budget.update", resource=budget_id, after=fields)
    return ok({"message": "Budget updated", "item": resp.get("Attributes", {})})


def delete_budget(budget_id: str, event: dict[str, Any], principal: auth.Principal) -> dict[str, Any]:
    """Delete a budget by ID."""
    try:
        _budgets_table().delete_item(
            Key={"budget_id": budget_id, "scope": "CONFIG"},
            ConditionExpression="attribute_exists(budget_id)",
        )
    except ClientError as e:
        if _client_error_code(e) == "ConditionalCheckFailedException":
            raise errors.NotFoundError(f"Budget {budget_id} not found") from e
        raise _upstream(e, "delete_budget") from e

    _audit(event, principal, action="budget.delete", resource=budget_id)
    return ok({"message": f"Budget {budget_id} deleted"})


# ── Usage queries ────────────────────────────────────────────────────────────


def get_usage(scope: str, scope_id: str) -> dict[str, Any]:
    """Get usage for the current period for a given scope/entity."""
    full_scope_id = f"{scope}#{scope_id}"
    period = datetime.now(tz=UTC).strftime("%Y-%m")

    try:
        resp = _usage_table().get_item(Key={"scope_id": full_scope_id, "period_date": period})
    except ClientError as e:
        raise _upstream(e, "get_usage") from e

    item = resp.get("Item")
    if not item:
        usage = UsageResponse(scope_id=full_scope_id, period_date=period)
        return ok(usage.model_dump(mode="json"))

    usage = UsageResponse(
        scope_id=item.get("scope_id", full_scope_id),
        period_date=item.get("period_date", period),
        total_cost_usd=Decimal(str(item.get("total_cost_usd", "0"))),
        total_tokens=int(item.get("total_tokens", 0)),
        input_tokens=int(item.get("input_tokens", 0)),
        output_tokens=int(item.get("output_tokens", 0)),
        cached_tokens=int(item.get("cached_tokens", 0)),
        request_count=int(item.get("request_count", 0)),
    )
    return ok(usage.model_dump(mode="json"))


def get_usage_history(
    scope: str,
    scope_id: str,
    query_params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Get daily usage breakdown for a scope/entity over a date range."""
    full_scope_id = f"{scope}#{scope_id}"
    now = datetime.now(tz=UTC)
    start_date = (query_params or {}).get("start_date", now.strftime("%Y-%m-01"))
    end_date = (query_params or {}).get("end_date", now.strftime("%Y-%m-%d"))

    try:
        from boto3.dynamodb.conditions import Key as DDBKey  # noqa: PLC0415

        resp = _usage_table().query(
            KeyConditionExpression=(
                DDBKey("scope_id").eq(full_scope_id) & DDBKey("period_date").between(start_date, end_date)
            ),
        )
    except ClientError as e:
        raise _upstream(e, "get_usage_history") from e

    items = resp.get("Items", [])
    usage_records = [
        UsageResponse(
            scope_id=item.get("scope_id", full_scope_id),
            period_date=item.get("period_date", ""),
            total_cost_usd=Decimal(str(item.get("total_cost_usd", "0"))),
            total_tokens=int(item.get("total_tokens", 0)),
            input_tokens=int(item.get("input_tokens", 0)),
            output_tokens=int(item.get("output_tokens", 0)),
            cached_tokens=int(item.get("cached_tokens", 0)),
            request_count=int(item.get("request_count", 0)),
        ).model_dump(mode="json")
        for item in items
    ]
    return ok({"items": usage_records, "count": len(usage_records)})
