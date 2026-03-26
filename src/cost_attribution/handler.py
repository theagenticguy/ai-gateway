"""Lambda handler for AI Gateway cost attribution."""

from __future__ import annotations

import base64
import gzip
import json
import logging
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import boto3
from botocore.exceptions import ClientError
from pydantic import ValidationError

from cost_attribution.models import HandlerResponse, LogRecord, MetricResult
from cost_attribution.pricing import get_cost

logger = logging.getLogger("cost_attribution")
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

METRIC_NAMESPACE = os.environ.get("METRIC_NAMESPACE", "AIGateway")
USAGE_TABLE = os.environ.get("USAGE_TABLE", "gateway-usage")
BUDGETS_TABLE = os.environ.get("BUDGETS_TABLE", "gateway-budgets")
SNS_TOPIC_ARN = os.environ.get("BUDGET_ALERTS_SNS_TOPIC_ARN", "")
AUDIT_FIREHOSE_STREAM = os.environ.get("AUDIT_FIREHOSE_STREAM", "")

cloudwatch = boto3.client("cloudwatch", region_name=os.environ.get("AWS_REGION", "us-east-1"))
dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
sns = boto3.client("sns", region_name=os.environ.get("AWS_REGION", "us-east-1"))

# Lazy-init Firehose client
_firehose_client = None


def _get_firehose():
    """Return a cached Firehose client (created on first call)."""
    global _firehose_client  # noqa: PLW0603
    if _firehose_client is None:
        _firehose_client = boto3.client("firehose", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return _firehose_client


# ── JWT claim extraction ─────────────────────────────────────────────────────


def _decode_jwt_claims(jwt_token: str) -> dict[str, Any]:
    """Decode JWT payload (base64 only, no verification -- ALB already verified)."""
    try:
        parts = jwt_token.split(".")
        if len(parts) < 2:  # noqa: PLR2004
            return {}
        payload = parts[1]
        # Add padding
        padding = 4 - len(payload) % 4
        if padding != 4:  # noqa: PLR2004
            payload += "=" * padding
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)  # type: ignore[no-any-return]
    except Exception:
        logger.debug("Failed to decode JWT claims")
        return {}


def _extract_identity(log_event_raw: dict[str, Any]) -> tuple[str, str]:
    """Extract (team, user) from log record's JWT header.

    Falls back to ("unknown", "unknown") if JWT is absent or unparseable.
    """
    try:
        req = log_event_raw.get("req", {})
        headers = req.get("headers", {}) if isinstance(req, dict) else {}
        jwt_token = headers.get("x-amzn-oidc-data", "") if isinstance(headers, dict) else ""
        if not jwt_token:
            return ("unknown", "unknown")

        claims = _decode_jwt_claims(jwt_token)
        team = claims.get("custom:team", claims.get("team", "unknown"))
        user = claims.get("sub", claims.get("username", "unknown"))
        return (str(team) if team else "unknown", str(user) if user else "unknown")
    except Exception:
        return ("unknown", "unknown")


# ── Log decoding & metric extraction ─────────────────────────────────────────


def _decode_log_data(event: dict[str, Any]) -> dict[str, Any]:
    return json.loads(gzip.decompress(base64.b64decode(event["awslogs"]["data"])))


def _extract_metrics(log_event: dict[str, Any]) -> MetricResult | None:
    try:
        message = log_event.get("message", "")
        raw = json.loads(message) if isinstance(message, str) else message
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None

    try:
        record = LogRecord.model_validate(raw)
    except ValidationError:
        return None

    if record.usage is None or not record.usage.has_tokens:
        return None

    provider = record.resolved_provider
    model = record.model
    team, user = _extract_identity(raw)

    from cost_attribution.pricing import get_cache_savings  # noqa: PLC0415

    cache_savings = get_cache_savings(
        provider,
        model,
        record.usage.cache_read_input_tokens,
        record.usage.cache_creation_input_tokens,
    )

    # Detect cache hit from gateway log (best-effort, defaults to False)
    cache_status = raw.get("cacheStatus") or raw.get("cache_status") or ""
    cache_hit = str(cache_status).upper() == "HIT"
    if not cache_hit:
        resp_cache = raw.get("response", {})
        if isinstance(resp_cache, dict):
            cache_hit = str(resp_cache.get("cache", "")).upper() == "HIT"

    return MetricResult(
        provider=provider,
        model=model,
        prompt_tokens=record.usage.prompt_tokens,
        completion_tokens=record.usage.completion_tokens,
        total_tokens=record.usage.total_tokens,
        cost_usd=get_cost(provider, model, record.usage.prompt_tokens, record.usage.completion_tokens),
        cache_read_input_tokens=record.usage.cache_read_input_tokens,
        cache_creation_input_tokens=record.usage.cache_creation_input_tokens,
        cache_savings_usd=cache_savings,
        cache_hit=cache_hit,
        team=team,
        user=user,
    )


# ── CloudWatch publishing ────────────────────────────────────────────────────


def _publish_metrics(metrics: list[MetricResult]) -> None:
    if not metrics:
        return
    metric_data: list[dict[str, Any]] = []
    for m in metrics:
        dims = [{"Name": "Provider", "Value": m.provider}, {"Name": "Model", "Value": m.model}]
        metric_data.extend(
            [
                {"MetricName": "TokensUsed", "Dimensions": dims, "Value": float(m.total_tokens), "Unit": "Count"},
                {"MetricName": "EstimatedCostUsd", "Dimensions": dims, "Value": m.cost_usd, "Unit": "None"},
                {"MetricName": "RequestCount", "Dimensions": dims, "Value": 1.0, "Unit": "Count"},
                {
                    "MetricName": "CachedReadTokens",
                    "Dimensions": dims,
                    "Value": float(m.cache_read_input_tokens),
                    "Unit": "Count",
                },
                {
                    "MetricName": "CachedWriteTokens",
                    "Dimensions": dims,
                    "Value": float(m.cache_creation_input_tokens),
                    "Unit": "Count",
                },
                {
                    "MetricName": "CacheSavingsUsd",
                    "Dimensions": dims,
                    "Value": m.cache_savings_usd,
                    "Unit": "None",
                },
            ]
        )

        # Per-team cache metrics
        team_dims = [{"Name": "Team", "Value": m.team}]
        if m.cache_hit:
            metric_data.append(
                {"MetricName": "CacheHitsByTeam", "Dimensions": team_dims, "Value": 1.0, "Unit": "Count"}
            )
        else:
            metric_data.append(
                {"MetricName": "CacheMissesByTeam", "Dimensions": team_dims, "Value": 1.0, "Unit": "Count"}
            )
        # Always publish cache savings per team (0 if no cache hit)
        metric_data.append(
            {"MetricName": "CacheSavingsByTeam", "Dimensions": team_dims, "Value": m.cache_savings_usd, "Unit": "None"}
        )
    for i in range(0, len(metric_data), 1000):
        cloudwatch.put_metric_data(Namespace=METRIC_NAMESPACE, MetricData=metric_data[i : i + 1000])
    logger.info("Published %d metric data points for %d requests", len(metric_data), len(metrics))


# ── DynamoDB usage accumulation ──────────────────────────────────────────────


def _accumulate_usage(metrics: list[MetricResult]) -> None:
    """Write usage increments to DynamoDB (best-effort).

    Writes both team-level (``USAGE#TEAM#<team>``) and user-level
    (``USAGE#USER#<user>``) rows using atomic ``ADD`` operations so
    concurrent Lambda invocations never lose counts.

    Also writes per-model usage rows (``USAGE#TEAM#<team>#MODEL#<model>``)
    to support E.5 model-level budget checks.
    """
    if not metrics:
        return

    table = dynamodb.Table(USAGE_TABLE)
    period = datetime.now(tz=UTC).strftime("%Y-%m")

    # Aggregate by (team, user) to minimise DynamoDB round-trips
    team_agg: dict[str, dict[str, int | float]] = {}
    user_agg: dict[str, dict[str, int | float]] = {}
    # E.5: aggregate by (team, model) for model-level tracking
    model_agg: dict[tuple[str, str], dict[str, int | float]] = {}

    for m in metrics:
        for key, agg in ((m.team, team_agg), (m.user, user_agg)):
            if key not in agg:
                agg[key] = {
                    "total_tokens": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_tokens": 0,
                    "total_cost_usd": 0.0,
                    "request_count": 0,
                }
            agg[key]["total_tokens"] += m.total_tokens
            agg[key]["input_tokens"] += m.prompt_tokens
            agg[key]["output_tokens"] += m.completion_tokens
            agg[key]["cached_tokens"] += m.cache_read_input_tokens + m.cache_creation_input_tokens
            agg[key]["total_cost_usd"] = float(agg[key]["total_cost_usd"]) + m.cost_usd
            agg[key]["request_count"] += 1

        # Model-level aggregation
        model_key = (m.team, m.model)
        if model_key not in model_agg:
            model_agg[model_key] = {
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_cost_usd": 0.0,
                "request_count": 0,
            }
        model_agg[model_key]["total_tokens"] += m.total_tokens
        model_agg[model_key]["input_tokens"] += m.prompt_tokens
        model_agg[model_key]["output_tokens"] += m.completion_tokens
        model_agg[model_key]["total_cost_usd"] = float(model_agg[model_key]["total_cost_usd"]) + m.cost_usd
        model_agg[model_key]["request_count"] += 1

    def _update(pk: str, sk: str, vals: dict[str, int | float]) -> None:
        expr_parts = []
        attr_vals: dict[str, Any] = {}
        field_map = {
            "total_tokens": ":tt",
            "input_tokens": ":it",
            "output_tokens": ":ot",
            "cached_tokens": ":ct",
            "total_cost_usd": ":tc",
            "request_count": ":rc",
        }
        for field_name, placeholder in field_map.items():
            if field_name in vals:
                expr_parts.append(f"{field_name} {placeholder}")
                val = vals[field_name]
                attr_vals[placeholder] = Decimal(str(round(val, 10))) if field_name == "total_cost_usd" else val

        table.update_item(
            Key={"pk": pk, "sk": sk},
            UpdateExpression="ADD " + ", ".join(expr_parts),
            ExpressionAttributeValues=attr_vals,
        )

    for team, vals in team_agg.items():
        try:
            _update(f"USAGE#TEAM#{team}", f"PERIOD#{period}", vals)
        except Exception:
            logger.warning("Failed to write team usage for %s", team, exc_info=True)

    for user, vals in user_agg.items():
        try:
            _update(f"USAGE#USER#{user}", f"PERIOD#{period}", vals)
        except Exception:
            logger.warning("Failed to write user usage for %s", user, exc_info=True)

    # E.5: Write model-level usage
    for (team, model), vals in model_agg.items():
        try:
            _update(f"USAGE#TEAM#{team}#MODEL#{model}", f"PERIOD#{period}", vals)
        except Exception:
            logger.warning("Failed to write model usage for team=%s model=%s", team, model, exc_info=True)


# ── E.6: Budget alert checking & SNS publishing ─────────────────────────────


def _get_budget_record(team: str) -> dict[str, Any] | None:
    """Fetch the budget configuration for a team from DynamoDB."""
    table = dynamodb.Table(BUDGETS_TABLE)
    resp = table.get_item(Key={"pk": f"BUDGET#{team}", "sk": "CONFIG"})
    return resp.get("Item")


def _find_top_model(metrics: list[MetricResult], team: str) -> str:
    """Find the model with the highest cost for a team in this batch."""
    model_costs: dict[str, float] = {}
    for m in metrics:
        if m.team == team:
            model_costs[m.model] = model_costs.get(m.model, 0.0) + m.cost_usd
    if not model_costs:
        return "unknown"
    return max(model_costs, key=lambda k: model_costs[k])


def _get_team_spend(team: str, period: str) -> Decimal | None:
    """Fetch the current-period spend for a team. Returns None on failure."""
    usage_table = dynamodb.Table(USAGE_TABLE)
    try:
        resp = usage_table.get_item(Key={"pk": f"USAGE#TEAM#{team}", "sk": f"PERIOD#{period}"})
        item = resp.get("Item")
        return Decimal(str(item.get("total_cost_usd", "0"))) if item else Decimal(0)
    except (ClientError, Exception):
        logger.warning("Failed to fetch usage for alert check (team=%s)", team, exc_info=True)
        return None


def _extract_alert_context(budget_item: dict[str, Any]) -> tuple[Decimal, list[int], list[int]] | None:
    """Extract budget amount, thresholds, and already-sent alerts from a budget record.

    Returns None if the record is invalid or has no usable budget.
    """
    try:
        monthly_budget = Decimal(str(budget_item.get("monthly_budget_usd", "0")))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if monthly_budget <= 0:
        return None
    alert_thresholds: list[int] = budget_item.get("alert_thresholds", [50, 80, 100])
    alerts_sent: list[int] = budget_item.get("alerts_sent", [])
    return monthly_budget, alert_thresholds, alerts_sent


def _process_team_alerts(
    team: str,
    metrics: list[MetricResult],
    period: str,
) -> int:
    """Check a single team's budget and publish alerts. Returns count of alerts published."""
    try:
        budget_item = _get_budget_record(team)
    except (ClientError, Exception):
        logger.warning("Failed to fetch budget record for alert check (team=%s)", team, exc_info=True)
        return 0

    if not budget_item:
        return 0

    ctx = _extract_alert_context(budget_item)
    if ctx is None:
        return 0
    monthly_budget, alert_thresholds, alerts_sent = ctx

    current_spend = _get_team_spend(team, period)
    if current_spend is None:
        return 0

    utilization_pct = float(current_spend / monthly_budget * 100)
    new_alerts = detect_crossed_thresholds(utilization_pct, alert_thresholds, alerts_sent)
    if not new_alerts:
        return 0

    top_model = _find_top_model(metrics, team)
    alerts_published = 0
    alert_ctx = {"budget": monthly_budget, "period": period, "top_model": top_model}

    for threshold in new_alerts:
        try:
            _publish_alert(team, threshold, current_spend, alert_ctx)
            alerts_published += 1
        except (ClientError, Exception):
            logger.warning("Failed to publish alert for team=%s threshold=%d", team, threshold, exc_info=True)

    # Update alerts_sent in DynamoDB
    all_sent = sorted(set(alerts_sent + new_alerts))
    try:
        budgets_table = dynamodb.Table(BUDGETS_TABLE)
        budgets_table.update_item(
            Key={"pk": f"BUDGET#{team}", "sk": "CONFIG"},
            UpdateExpression="SET alerts_sent = :as",
            ExpressionAttributeValues={":as": all_sent},
        )
    except (ClientError, Exception):
        logger.warning("Failed to update alerts_sent for team=%s", team, exc_info=True)

    return alerts_published


def check_and_publish_alerts(metrics: list[MetricResult]) -> int:
    """Check budget thresholds and publish SNS alerts for newly crossed thresholds.

    Returns the number of alerts published.
    """
    if not SNS_TOPIC_ARN or not metrics:
        return 0

    teams_in_batch: set[str] = {m.team for m in metrics if m.team != "unknown"}
    period = datetime.now(tz=UTC).strftime("%Y-%m")

    return sum(_process_team_alerts(team, metrics, period) for team in teams_in_batch)


def detect_crossed_thresholds(
    utilization_pct: float,
    alert_thresholds: list[int],
    alerts_sent: list[int],
) -> list[int]:
    """Determine which thresholds are newly crossed.

    Returns a sorted list of threshold values that have been crossed
    but not yet alerted on.
    """
    alerts_sent_set = set(alerts_sent)
    crossed = [t for t in alert_thresholds if utilization_pct >= t and t not in alerts_sent_set]
    return sorted(crossed)


def _publish_alert(
    team: str,
    threshold: int,
    current_spend: Decimal,
    ctx: dict[str, Any],
) -> None:
    """Publish a single budget alert to SNS.

    ``ctx`` must contain: ``budget`` (Decimal), ``period`` (str), ``top_model`` (str).
    """
    monthly_budget: Decimal = ctx["budget"]
    period: str = ctx["period"]
    message = {
        "type": "budget_alert",
        "team": team,
        "threshold_pct": threshold,
        "current_spend_usd": str(current_spend),
        "monthly_budget_usd": str(monthly_budget),
        "utilization_pct": round(float(current_spend / monthly_budget * 100), 1),
        "period": period,
        "top_model_by_cost": ctx["top_model"],
        "timestamp": datetime.now(tz=UTC).isoformat(),
    }
    sns.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=f"Budget Alert: {team} at {threshold}% ({period})",
        Message=json.dumps(message, default=str),
        MessageAttributes={
            "team": {"DataType": "String", "StringValue": team},
            "threshold": {"DataType": "Number", "StringValue": str(threshold)},
        },
    )
    logger.info("Published budget alert for team=%s threshold=%d%%", team, threshold)


# ── Audit record publishing (Kinesis Firehose) ───────────────────────────────


def _publish_audit_records(metrics: list[MetricResult]) -> None:
    """Publish audit records to Kinesis Firehose (best-effort)."""
    if not AUDIT_FIREHOSE_STREAM or not metrics:
        return

    firehose = _get_firehose()
    records = []
    for m in metrics:
        record = {
            "team": m.team,
            "user_id": m.user,
            "model": m.model,
            "provider": m.provider,
            "prompt_tokens": m.prompt_tokens,
            "completion_tokens": m.completion_tokens,
            "total_tokens": m.total_tokens,
            "cost_usd": m.cost_usd,
            "cache_read_tokens": m.cache_read_input_tokens,
            "cache_savings_usd": m.cache_savings_usd,
            "latency_ms": 0,  # Not available in current log format
            "status": "success",
            "correlation_id": str(uuid.uuid4()),  # Generate if not in log
            "request_timestamp": datetime.now(tz=UTC).isoformat(),
        }
        records.append({"Data": json.dumps(record).encode("utf-8")})

    # Firehose put_record_batch: max 500 records per call
    for i in range(0, len(records), 500):
        batch = records[i : i + 500]
        firehose.put_record_batch(
            DeliveryStreamName=AUDIT_FIREHOSE_STREAM,
            Records=batch,
        )

    logger.info("Published %d audit records to Firehose", len(records))


# ── Lambda entry point ───────────────────────────────────────────────────────


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        log_data = _decode_log_data(event)
    except Exception:
        logger.exception("Failed to decode log event payload")
        return HandlerResponse(statusCode=400, error="Failed to decode log data").model_dump(exclude_none=True)

    log_events = log_data.get("logEvents", [])
    logger.info("Processing %d log events from log group %s", len(log_events), log_data.get("logGroup", "unknown"))

    extracted: list[MetricResult] = []
    errors = 0
    for log_event in log_events:
        try:
            m = _extract_metrics(log_event)
            if m:
                extracted.append(m)
        except Exception:
            logger.exception("Failed to process log event: %s", log_event.get("id", "unknown"))
            errors += 1

    if extracted:
        try:
            _publish_metrics(extracted)
        except Exception:
            logger.exception("Failed to publish metrics to CloudWatch")
            return HandlerResponse(
                statusCode=500, processed=len(extracted), errors=errors, error="Failed to publish metrics"
            ).model_dump(exclude_none=True)

        # Best-effort DynamoDB accumulation -- never blocks the main flow
        try:
            _accumulate_usage(extracted)
        except Exception:
            logger.warning("Failed to accumulate usage in DynamoDB", exc_info=True)

        # E.6: Best-effort budget alert checking
        try:
            alerts_count = check_and_publish_alerts(extracted)
            if alerts_count > 0:
                logger.info("Published %d budget alerts", alerts_count)
        except Exception:
            logger.warning("Failed to check/publish budget alerts", exc_info=True)

        # Best-effort audit log publishing
        try:
            _publish_audit_records(extracted)
        except Exception:
            logger.warning("Failed to publish audit records to Firehose", exc_info=True)

    return HandlerResponse(
        statusCode=200,
        total_events=len(log_events),
        processed=len(extracted),
        skipped=len(log_events) - len(extracted) - errors,
        errors=errors,
    ).model_dump(exclude_none=True)
