"""Real-time usage self-service API Lambda handler.

Provides read-only access to team usage data stored in DynamoDB:
- Current period usage for a team
- Trailing N months of usage history
- Per-model usage breakdown for the current period
- Budget utilization percentage (when a budget config exists)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from usage_api.models import ModelUsage, UsagePeriod, UsageResponse

logger = logging.getLogger("usage_api")
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

USAGE_TABLE = os.environ.get("USAGE_TABLE", "gateway-usage")
BUDGETS_TABLE = os.environ.get("BUDGETS_TABLE", "gateway-budgets")

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))


# -- DynamoDB helpers ----------------------------------------------------------


def _get_team_usage(team: str, period: str) -> dict[str, Any] | None:
    """Fetch usage for a team in a single period from DynamoDB."""
    table = dynamodb.Table(USAGE_TABLE)
    resp = table.get_item(Key={"pk": f"USAGE#TEAM#{team}", "sk": f"PERIOD#{period}"})
    return resp.get("Item")


def _get_budget_config(team: str) -> dict[str, Any] | None:
    """Fetch the budget configuration for a team from DynamoDB."""
    table = dynamodb.Table(BUDGETS_TABLE)
    resp = table.get_item(Key={"pk": f"BUDGET#{team}", "sk": "CONFIG"})
    return resp.get("Item")


def _get_model_usage_for_team(team: str, period: str) -> list[dict[str, Any]]:
    """Scan for all model-level usage rows for a team in a given period.

    DDB pattern: PK begins_with ``USAGE#TEAM#{team}#MODEL#``, SK = ``PERIOD#{period}``
    """
    table = dynamodb.Table(USAGE_TABLE)
    items: list[dict[str, Any]] = []

    response = table.scan(
        FilterExpression=Key("pk").begins_with(f"USAGE#TEAM#{team}#MODEL#") & Key("sk").eq(f"PERIOD#{period}"),
    )
    items.extend(response.get("Items", []))

    while "LastEvaluatedKey" in response:
        response = table.scan(
            FilterExpression=Key("pk").begins_with(f"USAGE#TEAM#{team}#MODEL#") & Key("sk").eq(f"PERIOD#{period}"),
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

    Extracts the model name from the PK: ``USAGE#TEAM#{team}#MODEL#{model}``.
    """
    pk = item.get("pk", "")
    marker = "#MODEL#"
    idx = pk.find(marker)
    if idx == -1:
        return None
    model_name = pk[idx + len(marker) :]
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


# -- Route handlers ------------------------------------------------------------


def _handle_usage(team: str, history: int, models: bool) -> dict[str, Any]:
    """Core handler logic: fetch usage data and build the response."""
    period = _current_period()

    # Fetch current period usage
    try:
        current_item = _get_team_usage(team, period)
    except (ClientError, Exception):
        logger.exception("Failed to query current usage for team=%s", team)
        return _build_response(502, {"error": "Failed to query usage data"})

    current_period = _item_to_usage_period(current_item, period) if current_item else None

    # Fetch budget config for utilization calculation
    budget_utilization_pct: float | None = None
    monthly_budget_usd: Decimal | None = None
    try:
        budget_item = _get_budget_config(team)
        if budget_item:
            raw_budget = budget_item.get("monthly_budget_usd", "0")
            monthly_budget_usd = _safe_decimal(raw_budget)
            if monthly_budget_usd > 0 and current_period:
                budget_utilization_pct = round(
                    float(current_period.total_cost_usd / monthly_budget_usd * 100),
                    1,
                )
    except (ClientError, Exception):
        logger.exception("Failed to query budget config for team=%s", team)
        # Non-fatal: we still return usage data without budget info

    # Build history if requested
    usage_history: list[UsagePeriod] = []
    if history > 0:
        periods = _trailing_periods(history)
        for p in periods:
            try:
                item = _get_team_usage(team, p)
                if item:
                    usage_history.append(_item_to_usage_period(item, p))
            except (ClientError, Exception):
                logger.exception("Failed to query usage for team=%s period=%s", team, p)
                return _build_response(502, {"error": "Failed to query usage data"})

    # Build model breakdown if requested
    model_list: list[ModelUsage] = []
    if models:
        try:
            model_items = _get_model_usage_for_team(team, period)
            for item in model_items:
                mu = _item_to_model_usage(item)
                if mu:
                    model_list.append(mu)
            # Sort by cost descending for convenience
            model_list.sort(key=lambda m: m.total_cost_usd, reverse=True)
        except (ClientError, Exception):
            logger.exception("Failed to query model usage for team=%s", team)
            return _build_response(502, {"error": "Failed to query usage data"})

    response = UsageResponse(
        team=team,
        current_period=current_period,
        history=usage_history,
        models=model_list,
        budget_utilization_pct=budget_utilization_pct,
        monthly_budget_usd=monthly_budget_usd,
    )

    return _build_response(200, response.model_dump(mode="json"))


# -- Response builder ----------------------------------------------------------


def _build_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    """Build a Lambda Function URL response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


# -- Lambda entry point --------------------------------------------------------


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Lambda Function URL handler for the usage self-service API.

    Routes:
        GET /usage?team=X              -- current period usage for team X
        GET /usage?team=X&history=N    -- trailing N months of usage history
        GET /usage?team=X&models=true  -- per-model breakdown for current period
    """
    params = event.get("queryStringParameters") or {}
    team = params.get("team", "")

    if not team:
        return _build_response(400, {"error": "Missing required parameter: team"})

    history = 0
    try:
        history = int(params.get("history", "0"))
    except (ValueError, TypeError):
        history = 0

    models = params.get("models", "").lower() == "true"

    logger.info("Usage request: team=%s history=%d models=%s", team, history, models)

    return _handle_usage(team, history, models)
