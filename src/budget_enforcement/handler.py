"""Pre-request budget enforcement Lambda (Function URL).

Called by the gateway before forwarding a request to the upstream LLM.
Returns 200 with ``{"allowed": true}`` if the team/user is within budget,
or 429 with a rich error body if the budget is exceeded.

Graceful degradation: if DynamoDB is unreachable the request is allowed
and a warning is logged.
"""

from __future__ import annotations

import json
import logging
import os
from calendar import monthrange
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import boto3
from botocore.exceptions import ClientError
from pydantic import ValidationError

from budget_enforcement.jwt_utils import (
    decode_jwt_payload,
    extract_cost_center,
    extract_team,
    extract_tenant_tier,
    extract_user,
)
from budget_enforcement.models import BudgetCheckRequest, BudgetCheckResponse, BudgetStatus

logger = logging.getLogger("budget_enforcement")
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

BUDGETS_TABLE = os.environ.get("BUDGETS_TABLE", "gateway-budgets")
USAGE_TABLE = os.environ.get("USAGE_TABLE", "gateway-usage")

# Tier defaults (monthly budget in USD)
TIER_DEFAULTS: dict[str, Decimal] = {
    "free": Decimal(os.environ.get("TIER_DEFAULT_FREE", "10")),
    "standard": Decimal(os.environ.get("TIER_DEFAULT_STANDARD", "1000")),
    "premium": Decimal(os.environ.get("TIER_DEFAULT_PREMIUM", "10000")),
    "enterprise": Decimal(os.environ.get("TIER_DEFAULT_ENTERPRISE", "100000")),
}

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))


# ── DynamoDB helpers ─────────────────────────────────────────────────────────


def _get_budget_record(team: str) -> dict[str, Any] | None:
    """Fetch the budget configuration for a team from DynamoDB."""
    table = dynamodb.Table(BUDGETS_TABLE)
    resp = table.get_item(Key={"pk": f"BUDGET#{team}", "sk": "CONFIG"})
    return resp.get("Item")


def _get_current_usage(team: str) -> Decimal:
    """Fetch the current-period spend for a team from DynamoDB."""
    table = dynamodb.Table(USAGE_TABLE)
    period = datetime.now(tz=UTC).strftime("%Y-%m")
    resp = table.get_item(Key={"pk": f"USAGE#TEAM#{team}", "sk": f"PERIOD#{period}"})
    item = resp.get("Item")
    if not item:
        return Decimal("0.00")
    try:
        return Decimal(str(item.get("total_cost_usd", "0")))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def _seconds_until_period_reset() -> int:
    """Seconds remaining until the start of next month (UTC)."""
    now = datetime.now(tz=UTC)
    _, days_in_month = monthrange(now.year, now.month)
    end_of_month = now.replace(day=days_in_month, hour=23, minute=59, second=59)
    delta = end_of_month - now
    return max(1, int(delta.total_seconds()))


# ── Core budget check ────────────────────────────────────────────────────────


def _check_budget(request: BudgetCheckRequest) -> BudgetCheckResponse:
    """Run the budget check logic.

    1. Decode JWT and extract identity claims.
    2. Look up the team's budget record in DynamoDB (fall back to tier defaults).
    3. Compare current spend against the budget.
    4. Return allow/deny decision.
    """
    claims = decode_jwt_payload(request.jwt_token)
    team = extract_team(claims)
    user = extract_user(claims)
    cost_center = extract_cost_center(claims)
    tenant_tier = extract_tenant_tier(claims)

    # Fetch budget config (DynamoDB failure → graceful allow)
    try:
        budget_item = _get_budget_record(team)
    except (ClientError, Exception):
        logger.warning("DynamoDB unreachable for budget lookup (team=%s), allowing request", team, exc_info=True)
        return BudgetCheckResponse(allowed=True, reason="budget-check-degraded")

    if budget_item:
        try:
            monthly_budget = Decimal(str(budget_item.get("monthly_budget_usd", "1000")))
        except (InvalidOperation, TypeError, ValueError):
            monthly_budget = Decimal(1000)
        warn_pct = float(budget_item.get("warn_threshold_pct", 80))
        hard_pct = float(budget_item.get("hard_limit_pct", 100))
    else:
        # Fall back to tier defaults
        monthly_budget = TIER_DEFAULTS.get(tenant_tier, TIER_DEFAULTS["standard"])
        warn_pct = 80.0
        hard_pct = 100.0

    # Fetch current spend (DynamoDB failure → graceful allow)
    try:
        current_spend = _get_current_usage(team)
    except (ClientError, Exception):
        logger.warning("DynamoDB unreachable for usage lookup (team=%s), allowing request", team, exc_info=True)
        return BudgetCheckResponse(allowed=True, reason="budget-check-degraded")

    utilization_pct = float(current_spend / monthly_budget * 100) if monthly_budget > 0 else 0.0

    budget_status = BudgetStatus(
        team=team,
        user=user,
        cost_center=cost_center,
        tenant_tier=tenant_tier,
        monthly_budget_usd=monthly_budget,
        current_spend_usd=current_spend,
        utilization_pct=utilization_pct,
        warn_threshold_pct=warn_pct,
        hard_limit_pct=hard_pct,
    )

    # Hard limit exceeded → block
    if utilization_pct >= hard_pct:
        logger.info(
            "Budget exceeded for team=%s (%.1f%% >= %.1f%% of $%s)",
            team,
            utilization_pct,
            hard_pct,
            monthly_budget,
        )
        return BudgetCheckResponse(
            allowed=False,
            status_code=429,
            reason=f"Monthly budget exceeded ({utilization_pct:.1f}% of ${monthly_budget})",
            budget_status=budget_status,
            retry_after_seconds=_seconds_until_period_reset(),
        )

    # Warning threshold → allow with warning
    if utilization_pct >= warn_pct:
        logger.info(
            "Budget warning for team=%s (%.1f%% >= %.1f%% of $%s)",
            team,
            utilization_pct,
            warn_pct,
            monthly_budget,
        )
        return BudgetCheckResponse(
            allowed=True,
            reason=f"Budget warning ({utilization_pct:.1f}% of ${monthly_budget})",
            budget_status=budget_status,
        )

    return BudgetCheckResponse(allowed=True, budget_status=budget_status)


# ── Lambda entry point (Function URL) ────────────────────────────────────────


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Lambda Function URL handler.

    Expects a JSON body with ``jwt_token`` (and optionally ``model``,
    ``provider``, ``estimated_tokens``).
    """
    try:
        body_str = event.get("body", "{}")
        if event.get("isBase64Encoded"):
            import base64  # noqa: PLC0415

            body_str = base64.b64decode(body_str).decode()
        body = json.loads(body_str) if isinstance(body_str, str) else body_str
    except (json.JSONDecodeError, Exception):
        logger.exception("Failed to parse request body")
        resp = BudgetCheckResponse(
            allowed=False,
            status_code=400,
            reason="Invalid request body",
        )
        return _build_response(resp)

    try:
        request = BudgetCheckRequest.model_validate(body)
    except ValidationError as e:
        logger.warning("Invalid budget check request: %s", e)
        resp = BudgetCheckResponse(
            allowed=False,
            status_code=400,
            reason=f"Validation error: {e.error_count()} errors",
        )
        return _build_response(resp)

    result = _check_budget(request)
    return _build_response(result)


def _build_response(result: BudgetCheckResponse) -> dict[str, Any]:
    """Format the Lambda Function URL response."""
    status = result.status_code
    body = result.model_dump(exclude_none=True, mode="json")
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
