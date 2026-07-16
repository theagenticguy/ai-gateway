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
from gwcore.tiers import TIER_DEFAULTS as GWCORE_TIER_DEFAULTS
from rate_limiter.handler import check_rate_limit

logger = get_logger("budget_enforcement")

BUDGETS_TABLE = os.environ.get("BUDGETS_TABLE", "gateway-budgets")
USAGE_TABLE = os.environ.get("USAGE_TABLE", "gateway-usage")

# The budget's configured USD amount IS the hard cap (100%); admin has no
# separate hard-limit percentage. Warn thresholds are the alert points below it.
_HARD_LIMIT_PCT = 100

# ── Tier defaults ────────────────────────────────────────────────────────────
# Tier defaults come from gwcore.tiers (the single source of truth, issue #260)
# and can be overridden per-deployment via the TIER_DEFAULTS env var (JSON).

_DEFAULT_TIER_DEFAULTS: dict[str, TierConfig] = {
    t.value: TierConfig.model_validate(d) for t, d in GWCORE_TIER_DEFAULTS.items()
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

    return dict(_DEFAULT_TIER_DEFAULTS)


TIER_DEFAULTS: dict[str, TierConfig] = _load_tier_defaults()

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))


# ── DynamoDB helpers ─────────────────────────────────────────────────────────


def _budget_warn_pct(alert_thresholds: Any) -> float:
    """Derive a warn threshold from budget_admin's ``alert_thresholds`` list.

    Admin stores a list of percent ints (e.g. ``[50, 80, 100]``). We treat the
    highest threshold that is strictly below 100 as the warn point (80 here);
    100 is the hard cap, not a warning. Falls back to 80 when nothing qualifies.
    """
    if isinstance(alert_thresholds, (list, tuple)):
        below_cap = [int(t) for t in alert_thresholds if _is_int_like(t) and int(t) < _HARD_LIMIT_PCT]
        if below_cap:
            return float(max(below_cap))
    return 80.0


def _is_int_like(value: Any) -> bool:
    try:
        int(value)
    except (TypeError, ValueError):
        return False
    return True


def _adapt_admin_budget(item: dict[str, Any]) -> dict[str, Any]:
    """Translate a budget_admin-written item into the field vocabulary this
    enforcement handler consumes.

    budget_admin (src/budget_admin/routes.py::create_budget) and this handler
    grew independent field names against the same ``gateway-budgets`` table
    (issue #261). Rather than reshape the table or the admin write path (whose
    keys are immutable / already correct), we adapt on read:

    - ``monthly_budget_usd`` <- ``budget_usd`` (the admin-configured cap)
    - ``warn_threshold_pct``  <- derived from ``alert_thresholds`` (highest < 100)
    - ``hard_limit_pct``      <- 100 (admin has no separate hard limit; the
      configured ``budget_usd`` IS the hard cap)
    - ``rpm`` / ``tokens_per_day`` are intentionally NOT synthesized: they are
      absent on the admin item, so the consumer falls back to tier_config.
    - ``model_limits`` is passed through unchanged; ``_parse_model_limits``
      accepts both the admin list form and the legacy dict form.
    """
    adapted = dict(item)
    if "budget_usd" in item and "monthly_budget_usd" not in item:
        adapted["monthly_budget_usd"] = item["budget_usd"]
    adapted.setdefault("warn_threshold_pct", _budget_warn_pct(item.get("alert_thresholds")))
    adapted.setdefault("hard_limit_pct", _HARD_LIMIT_PCT)
    return adapted


def _get_budget_record(team: str) -> dict[str, Any] | None:
    """Fetch the team-scoped budget configuration from DynamoDB (issue #261).

    Budgets are written by budget_admin keyed by ``budget_id`` (uuid) + ``scope``
    ("CONFIG"), with the entity id in ``scope_id`` and the entity kind in
    ``scope_type``. Enforcement looks a budget up by *team*, so we query the
    ``scope-index`` GSI (HASH=``scope``, RANGE=``scope_id``): every config item
    shares the partition value "CONFIG", and the range key is ``scope_id``, so
    ``scope == "CONFIG" AND scope_id == team`` returns the budgets for that
    entity id. We filter for ``scope_type == "team"`` to select the team budget
    (ignoring any user/project budget that happens to share the id) and return
    the first match, adapted to this handler's field vocabulary.
    """
    from boto3.dynamodb.conditions import Attr, Key  # noqa: PLC0415

    table = dynamodb.Table(BUDGETS_TABLE)
    resp = table.query(
        IndexName="scope-index",
        KeyConditionExpression=Key("scope").eq("CONFIG") & Key("scope_id").eq(team),
        FilterExpression=Attr("scope_type").eq("team"),
    )
    items = resp.get("Items", [])
    if not items:
        return None
    return _adapt_admin_budget(items[0])


def _get_current_usage(team: str) -> Decimal:
    """Fetch the current-period spend for a team from DynamoDB.

    Usage rows are keyed by the real Terraform schema (issue #261): the physical
    ``gateway-usage`` table is hash=``scope_id``, range=``period_date`` (see
    infrastructure/modules/budgets/main.tf). Team usage is written by
    cost_attribution under ``scope_id = f"team#{team}"`` with a monthly
    ``period_date`` of ``YYYY-MM`` — the same convention budget_admin reads.
    """
    table = dynamodb.Table(USAGE_TABLE)
    period = datetime.now(tz=UTC).strftime("%Y-%m")
    resp = table.get_item(Key={"scope_id": f"team#{team}", "period_date": period})
    item = resp.get("Item")
    if not item:
        return Decimal("0.00")
    try:
        return Decimal(str(item.get("total_cost_usd", "0")))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def _get_model_usage(team: str, model: str) -> Decimal:
    """Fetch the current-period spend for a specific model within a team.

    Per-model usage is written by cost_attribution under the conforming key
    ``scope_id = f"team#{team}#model#{model}"``, ``period_date = YYYY-MM``
    (issue #261, real ``gateway-usage`` schema).
    """
    table = dynamodb.Table(USAGE_TABLE)
    period = datetime.now(tz=UTC).strftime("%Y-%m")
    resp = table.get_item(Key={"scope_id": f"team#{team}#model#{model}", "period_date": period})
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
    """Parse model_limits from a DynamoDB budget record.

    Accepts BOTH shapes (issue #261), defensively:
    - Legacy enforcement dict form: ``{model_name: {"monthly_usd": ..., ...}}``
    - budget_admin list form: ``[{"model": name, "max_cost_usd": ...}, ...]``
      where ``max_cost_usd`` maps onto ``ModelLimit.monthly_usd``.
    """
    raw = budget_item.get("model_limits")
    result: dict[str, ModelLimit] = {}
    if isinstance(raw, dict):
        for model_name, limit_data in raw.items():
            if not isinstance(limit_data, dict):
                continue
            try:
                result[model_name] = ModelLimit.model_validate(limit_data)
            except ValidationError:
                logger.warning("Invalid model_limit for model=%s, skipping", model_name)
    elif isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict) or "model" not in entry:
                continue
            model_name = entry["model"]
            try:
                result[model_name] = ModelLimit.model_validate({"monthly_usd": entry.get("max_cost_usd", 0)})
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
