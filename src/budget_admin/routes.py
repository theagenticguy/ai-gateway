"""Route implementations for the Budget Admin API.

Each function corresponds to a REST endpoint and interacts with
DynamoDB budget and usage tables.
"""

from __future__ import annotations

import json
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
    ListResponse,
    UpdateBudgetRequest,
    UsageResponse,
)

logger = logging.getLogger("budget_admin.routes")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _client_error_code(e: ClientError) -> str:
    """Safely extract the error code from a ClientError response."""
    return e.response.get("Error", {}).get("Code", "Unknown")  # type: ignore[union-attr]


# Table names from environment (set in handler.py at init time)
_budgets_table_name: str = ""
_usage_table_name: str = ""
_dynamodb: Any = None


def init_dynamodb(budgets_table: str, usage_table: str, region: str = "us-east-1") -> None:
    """Initialize the DynamoDB resource and table names.

    Called once at Lambda cold start from handler.py.
    """
    global _budgets_table_name, _usage_table_name, _dynamodb  # noqa: PLW0603
    _budgets_table_name = budgets_table
    _usage_table_name = usage_table
    _dynamodb = boto3.resource("dynamodb", region_name=region)


def _budgets_table() -> Any:
    return _dynamodb.Table(_budgets_table_name)


def _usage_table() -> Any:
    return _dynamodb.Table(_usage_table_name)


# ── JSON serialization helper ────────────────────────────────────────────────


class _DecimalEncoder(json.JSONEncoder):
    """Encode Decimal values as float-safe strings for JSON output."""

    def default(self, o: object) -> Any:
        if isinstance(o, Decimal):
            return str(o)
        return super().default(o)


def _json_response(status_code: int, body: dict[str, Any] | list[Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, cls=_DecimalEncoder),
    }


def _error_response(status_code: int, message: str) -> dict[str, Any]:
    return _json_response(status_code, {"error": message})


# ── Budget CRUD ──────────────────────────────────────────────────────────────


def list_budgets(query_params: dict[str, str] | None = None) -> dict[str, Any]:
    """List all budgets with pagination (DynamoDB Scan, limit 25)."""
    params: dict[str, Any] = {"Limit": 25}

    if query_params and query_params.get("last_key"):
        try:
            last_key = json.loads(query_params["last_key"])
            params["ExclusiveStartKey"] = last_key
        except (json.JSONDecodeError, TypeError):
            return _error_response(400, "Invalid last_key pagination cursor")

    try:
        resp = _budgets_table().scan(**params)
    except ClientError as e:
        logger.exception("Failed to scan budgets table")
        return _error_response(502, f"DynamoDB error: {_client_error_code(e)}")

    items = resp.get("Items", [])
    last_evaluated_key = resp.get("LastEvaluatedKey")

    result = ListResponse(
        items=items,
        count=len(items),
        last_key=last_evaluated_key,
    )
    return _json_response(200, result.model_dump(exclude_none=True, mode="json"))


def get_budget(budget_id: str) -> dict[str, Any]:
    """Get a single budget by ID, including current-period usage."""
    try:
        resp = _budgets_table().get_item(Key={"budget_id": budget_id, "scope": "CONFIG"})
    except ClientError as e:
        logger.exception("Failed to get budget %s", budget_id)
        return _error_response(502, f"DynamoDB error: {_client_error_code(e)}")

    item = resp.get("Item")
    if not item:
        return _error_response(404, f"Budget {budget_id} not found")

    # Fetch current usage for this budget's scope_id
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
    return _json_response(200, budget_resp.model_dump(exclude_none=True, mode="json"))


def create_budget(body: dict[str, Any]) -> dict[str, Any]:
    """Create a new budget. Fails if a budget with the same ID already exists."""
    try:
        request = CreateBudgetRequest.model_validate(body)
    except ValidationError as e:
        errors = e.errors()
        return _error_response(400, f"Validation error: {json.dumps(errors, default=str)}")

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
        _budgets_table().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(budget_id)",
        )
    except ClientError as e:
        if _client_error_code(e) == "ConditionalCheckFailedException":
            return _error_response(409, f"Budget {budget_id} already exists")
        logger.exception("Failed to create budget")
        return _error_response(502, f"DynamoDB error: {_client_error_code(e)}")

    return _json_response(201, {"budget_id": budget_id, "message": "Budget created"})


def update_budget(budget_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Update a budget with partial fields via UpdateItem expression builder."""
    try:
        request = UpdateBudgetRequest.model_validate(body)
    except ValidationError as e:
        errors = e.errors()
        return _error_response(400, f"Validation error: {json.dumps(errors, default=str)}")

    # Build update expression from non-None fields
    update_parts: list[str] = []
    expr_names: dict[str, str] = {}
    expr_values: dict[str, Any] = {}

    fields = request.model_dump(exclude_none=True, mode="json")
    if not fields:
        return _error_response(400, "No fields to update")

    for field_name, value in fields.items():
        safe_name = f"#{field_name}"
        safe_value = f":{field_name}"
        update_parts.append(f"{safe_name} = {safe_value}")
        expr_names[safe_name] = field_name
        expr_values[safe_value] = value

    # Always update the updated_at timestamp
    update_parts.append("#updated_at = :updated_at")
    expr_names["#updated_at"] = "updated_at"
    expr_values[":updated_at"] = datetime.now(tz=UTC).isoformat()

    update_expression = "SET " + ", ".join(update_parts)

    try:
        resp = _budgets_table().update_item(
            Key={"budget_id": budget_id, "scope": "CONFIG"},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ConditionExpression="attribute_exists(budget_id)",
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if _client_error_code(e) == "ConditionalCheckFailedException":
            return _error_response(404, f"Budget {budget_id} not found")
        logger.exception("Failed to update budget %s", budget_id)
        return _error_response(502, f"DynamoDB error: {_client_error_code(e)}")

    return _json_response(200, {"message": "Budget updated", "item": resp.get("Attributes", {})})


def delete_budget(budget_id: str) -> dict[str, Any]:
    """Delete a budget by ID."""
    try:
        _budgets_table().delete_item(
            Key={"budget_id": budget_id, "scope": "CONFIG"},
            ConditionExpression="attribute_exists(budget_id)",
        )
    except ClientError as e:
        if _client_error_code(e) == "ConditionalCheckFailedException":
            return _error_response(404, f"Budget {budget_id} not found")
        logger.exception("Failed to delete budget %s", budget_id)
        return _error_response(502, f"DynamoDB error: {_client_error_code(e)}")

    return _json_response(200, {"message": f"Budget {budget_id} deleted"})


# ── Usage queries ────────────────────────────────────────────────────────────


def get_usage(scope: str, scope_id: str) -> dict[str, Any]:
    """Get usage for the current period for a given scope/entity."""
    full_scope_id = f"{scope}#{scope_id}"
    period = datetime.now(tz=UTC).strftime("%Y-%m")

    try:
        resp = _usage_table().get_item(Key={"scope_id": full_scope_id, "period_date": period})
    except ClientError as e:
        logger.exception("Failed to get usage for %s", full_scope_id)
        return _error_response(502, f"DynamoDB error: {_client_error_code(e)}")

    item = resp.get("Item")
    if not item:
        # Return zeroed usage rather than 404
        usage = UsageResponse(scope_id=full_scope_id, period_date=period)
        return _json_response(200, usage.model_dump(mode="json"))

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
    return _json_response(200, usage.model_dump(mode="json"))


def get_usage_history(
    scope: str,
    scope_id: str,
    query_params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Get daily usage breakdown for a scope/entity over a date range.

    Query parameters:
        start_date: Start of range (YYYY-MM-DD or YYYY-MM), defaults to current month start
        end_date: End of range (YYYY-MM-DD or YYYY-MM), defaults to today
    """
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
        logger.exception("Failed to query usage history for %s", full_scope_id)
        return _error_response(502, f"DynamoDB error: {_client_error_code(e)}")

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

    return _json_response(200, {"items": usage_records, "count": len(usage_records)})
