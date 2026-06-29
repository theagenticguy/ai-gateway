"""Pre-request budget enforcement Lambda (Function URL).

An agentgateway guardrail webhook (ADR-017): the data plane calls it before
forwarding a request to the upstream LLM. agentgateway POSTs
``{"body": {"messages": [...]}}`` and expects an ``action`` envelope back
(``pass`` / ``reject``). It ALWAYS returns HTTP 200; the action carries the
allow/deny decision, because a 4xx would be treated as a hook *failure*, not a
deny.

Graceful degradation: if DynamoDB is unreachable the request is allowed and a
warning is logged — a budget-check outage must never block traffic.

Migrated onto gwcore (ADR-016): structured JSON logging with a correlation id,
a latency Timer, deny metrics, and a deny-audit event on every block. The JWT
arrives as the forwarded ``x-amzn-oidc-data`` header (ALB pre-verifies its
signature), so this handler does no in-handler authorization — ``gwcore.auth``
(header-based) does not apply here.
"""

from __future__ import annotations

import json
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
from budget_enforcement.models import (
    BudgetCheckRequest,
    BudgetCheckResponse,
    BudgetStatus,
    ModelBudgetError,
    ModelLimit,
    TierConfig,
)
from gwcore import agentgateway, audit
from gwcore.logging import bind, correlation_id, get_logger
from gwcore.telemetry import Timer, emit_metric
from rate_limiter.handler import check_rate_limit

logger = get_logger("budget_enforcement")

BUDGETS_TABLE = os.environ.get("BUDGETS_TABLE", "gateway-budgets")
USAGE_TABLE = os.environ.get("USAGE_TABLE", "gateway-usage")

# ── Tier defaults ────────────────────────────────────────────────────────────
# E.4: Load tier defaults from TIER_DEFAULTS env var (JSON) or fall back to
# legacy per-tier env vars for backward compatibility.

_DEFAULT_TIER_DEFAULTS: dict[str, dict[str, Any]] = {
    "sandbox": {"rpm": 20, "tokens_per_day": 100000, "monthly_usd": 25},
    "standard": {"rpm": 100, "tokens_per_day": 500000, "monthly_usd": 100},
    "premium": {"rpm": 500, "tokens_per_day": 5000000, "monthly_usd": 1000},
    "unlimited": {"rpm": 2000, "tokens_per_day": -1, "monthly_usd": 10000},
}


def _load_tier_defaults() -> dict[str, TierConfig]:
    """Load tier defaults from the TIER_DEFAULTS env var (JSON) or built-in defaults."""
    raw = os.environ.get("TIER_DEFAULTS", "")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {k.lower(): TierConfig.model_validate(v) for k, v in parsed.items()}
        except (json.JSONDecodeError, ValidationError):
            logger.warning("Failed to parse TIER_DEFAULTS env var, using built-in defaults", exc_info=True)

    # Legacy fallback: per-tier env vars (monthly_usd only)
    legacy_free = os.environ.get("TIER_DEFAULT_FREE")
    if legacy_free is not None:
        return {
            "free": TierConfig(rpm=20, tokens_per_day=100000, monthly_usd=Decimal(legacy_free)),
            "standard": TierConfig(
                rpm=100,
                tokens_per_day=500000,
                monthly_usd=Decimal(os.environ.get("TIER_DEFAULT_STANDARD", "1000")),
            ),
            "premium": TierConfig(
                rpm=500,
                tokens_per_day=5000000,
                monthly_usd=Decimal(os.environ.get("TIER_DEFAULT_PREMIUM", "10000")),
            ),
            "enterprise": TierConfig(
                rpm=2000,
                tokens_per_day=-1,
                monthly_usd=Decimal(os.environ.get("TIER_DEFAULT_ENTERPRISE", "100000")),
            ),
        }

    return {k: TierConfig.model_validate(v) for k, v in _DEFAULT_TIER_DEFAULTS.items()}


TIER_DEFAULTS: dict[str, TierConfig] = _load_tier_defaults()

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


def _get_model_usage(team: str, model: str) -> Decimal:
    """Fetch the current-period spend for a specific model within a team."""
    table = dynamodb.Table(USAGE_TABLE)
    period = datetime.now(tz=UTC).strftime("%Y-%m")
    resp = table.get_item(Key={"pk": f"USAGE#TEAM#{team}#MODEL#{model}", "sk": f"PERIOD#{period}"})
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


# ── Model-level budget check (E.5) ──────────────────────────────────────────


def _parse_model_limits(budget_item: dict[str, Any]) -> dict[str, ModelLimit]:
    """Parse model_limits from a DynamoDB budget record."""
    raw = budget_item.get("model_limits")
    if not raw or not isinstance(raw, dict):
        return {}
    result: dict[str, ModelLimit] = {}
    for model_name, limit_data in raw.items():
        try:
            if isinstance(limit_data, dict):
                result[model_name] = ModelLimit.model_validate(limit_data)
        except ValidationError:
            logger.warning("Invalid model_limit for model=%s, skipping", model_name)
    return result


def _check_model_budget(team: str, model: str, model_limits: dict[str, ModelLimit]) -> ModelBudgetError | None:
    """Check if a model-level budget cap is exceeded.

    Returns a ``ModelBudgetError`` if the limit is exceeded, ``None`` otherwise.
    """
    if model == "unknown" or not model_limits:
        return None

    limit = model_limits.get(model)
    if limit is None:
        return None

    try:
        current_model_spend = _get_model_usage(team, model)
    except (ClientError, Exception):
        logger.warning("DynamoDB unreachable for model usage lookup (team=%s, model=%s)", team, model, exc_info=True)
        return None  # Graceful degradation

    if current_model_spend >= limit.monthly_usd:
        return ModelBudgetError(
            type="model_budget_exceeded",
            model=model,
            limit_usd=limit.monthly_usd,
            current_usd=current_model_spend,
        )
    return None


# ── Core budget check ────────────────────────────────────────────────────────


def _check_budget(request: BudgetCheckRequest) -> BudgetCheckResponse:
    """Run the budget check logic.

    1. Decode JWT and extract identity claims.
    2. Look up the team's budget record in DynamoDB (fall back to tier defaults).
    3. Resolve tier config (rate limits + budget defaults).
    4. Check rate limits (RPM + daily tokens).
    5. Compare current spend against the budget.
    6. Check model-level budgets if applicable.
    7. Return allow/deny decision.
    """
    claims = decode_jwt_payload(request.jwt_token)
    team = extract_team(claims)
    user = extract_user(claims)
    cost_center = extract_cost_center(claims)
    tenant_tier = extract_tenant_tier(claims)

    # Fetch budget config (DynamoDB failure -> graceful allow)
    try:
        budget_item = _get_budget_record(team)
    except (ClientError, Exception):
        logger.warning("DynamoDB unreachable for budget lookup (team=%s), allowing request", team, exc_info=True)
        return BudgetCheckResponse(allowed=True, reason="budget-check-degraded")

    model_limits: dict[str, ModelLimit] = {}

    # Resolve tier config (needed for rate limits and budget defaults)
    tier_config = TIER_DEFAULTS.get(tenant_tier)
    if tier_config is None:
        tier_config = TIER_DEFAULTS.get(
            "standard", TierConfig(rpm=100, tokens_per_day=500000, monthly_usd=Decimal(100))
        )

    if budget_item:
        try:
            monthly_budget = Decimal(str(budget_item.get("monthly_budget_usd", "1000")))
        except (InvalidOperation, TypeError, ValueError):
            monthly_budget = Decimal(1000)
        warn_pct = float(budget_item.get("warn_threshold_pct", 80))
        hard_pct = float(budget_item.get("hard_limit_pct", 100))
        model_limits = _parse_model_limits(budget_item)
        # Override tier defaults with budget-item-level rate limits if present
        tier_config = TierConfig(
            rpm=int(budget_item.get("rpm", tier_config.rpm)),
            tokens_per_day=int(budget_item.get("tokens_per_day", tier_config.tokens_per_day)),
            monthly_usd=monthly_budget,
        )
    else:
        # E.4: Fall back to tier defaults with full TierConfig
        monthly_budget = tier_config.monthly_usd
        warn_pct = 80.0
        hard_pct = 100.0

    # Rate limit check (RPM + daily tokens)
    rate_result = check_rate_limit(
        team=team,
        rpm_limit=tier_config.rpm,
        tokens_per_day_limit=tier_config.tokens_per_day,
        estimated_tokens=request.estimated_tokens,
    )
    if not rate_result.allowed:
        return BudgetCheckResponse(
            allowed=False,
            status_code=429,
            reason=rate_result.reason,
            retry_after_seconds=rate_result.retry_after_seconds,
        )

    # Fetch current spend (DynamoDB failure -> graceful allow)
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

    # Hard limit exceeded -> block
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

    # E.5: Check model-level budget caps
    if model_limits and request.model != "unknown":
        model_error = _check_model_budget(team, request.model, model_limits)
        if model_error is not None:
            logger.info(
                "Model budget exceeded for team=%s model=%s ($%s >= $%s)",
                team,
                request.model,
                model_error.current_usd,
                model_error.limit_usd,
            )
            return BudgetCheckResponse(
                allowed=False,
                status_code=429,
                reason=f"Model budget exceeded for {request.model}",
                budget_status=budget_status,
                error=model_error,
                retry_after_seconds=_seconds_until_period_reset(),
            )

    # Warning threshold -> allow with warning
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


def _audit_denial(event: dict[str, Any], request: BudgetCheckRequest, result: BudgetCheckResponse) -> None:
    """Emit a deny metric + audit event for a blocked request (best-effort).

    Only fires on a hard deny. Graceful-degradation allows and warning-threshold
    allows are not denials, so they are not audited here.
    """
    if result.allowed:
        return
    claims = decode_jwt_payload(request.jwt_token)
    team = extract_team(claims)
    actor = extract_user(claims)
    emit_metric("BudgetDenied", 1, dimensions={"Route": "budget_enforcement"})
    audit.emit(
        audit.event_from_request(
            event,
            action="budget.enforce",
            actor=actor,
            resource=request.model,
            decision="deny",
            status=result.status_code,
            team=team,
            detail=result.reason,
        )
    )


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Lambda Function URL handler (agentgateway guardrail webhook, ADR-017).

    agentgateway posts ``{"body": {"messages": [...]}}`` and expects an
    ``action`` envelope back; the JWT arrives as the forwarded
    ``x-amzn-oidc-data`` header and the request model/tokens are derived from
    the messages + headers.
    """
    cid = correlation_id(event)
    log = bind(logger, cid)

    with Timer("RequestLatency", route="budget_enforcement"):
        try:
            body_str = event.get("body", "{}")
            if event.get("isBase64Encoded"):
                import base64  # noqa: PLC0415

                body_str = base64.b64decode(body_str).decode()
            body = json.loads(body_str) if isinstance(body_str, str) else body_str
        except (json.JSONDecodeError, Exception):
            log.exception("Failed to parse request body")
            emit_metric("BudgetEnforcementError", 1, dimensions={"Code": "bad_request"})
            resp = BudgetCheckResponse(allowed=False, status_code=400, reason="Invalid request body")
            return _build_agentgateway_response(resp)

        request = _request_from_agentgateway(body, event)

        result = _check_budget(request)
        _audit_denial(event, request, result)

        return _build_agentgateway_response(result)


def _request_from_agentgateway(body: dict[str, Any], event: dict[str, Any]) -> BudgetCheckRequest:
    """Build a BudgetCheckRequest from an agentgateway guardrail call.

    The JWT is the forwarded ``x-amzn-oidc-data`` header. agentgateway does not
    send the resolved model or a token count, so tokens are estimated from the
    message text and the model is read from a forwarded ``x-model`` header when
    present (else ``unknown``, which disables only per-model caps, not the
    team-level hard stop).
    """
    messages = agentgateway.extract_messages(body)
    jwt = agentgateway.header_lookup(event, "x-amzn-oidc-data")
    model = agentgateway.header_lookup(event, "x-model") or "unknown"
    return BudgetCheckRequest(
        jwt_token=jwt,
        model=model,
        estimated_tokens=agentgateway.estimate_tokens(messages),
    )


def _build_agentgateway_response(result: BudgetCheckResponse) -> dict[str, Any]:
    """Map a budget decision onto agentgateway's action envelope.

    Allow (including the warn-threshold and graceful-degradation allows) maps to
    ``pass``. A hard deny maps to ``reject`` with the budget's status code and a
    JSON body carrying the reason + retry-after, so the client sees a 429.
    """
    if result.allowed:
        return agentgateway.pass_action()
    payload: dict[str, Any] = {"error": result.reason or "budget exceeded"}
    if result.retry_after_seconds:
        payload["retry_after_seconds"] = result.retry_after_seconds
    return agentgateway.reject_action(
        status_code=result.status_code or 429,
        body=json.dumps(payload),
        reason=result.reason or "budget exceeded",
    )
