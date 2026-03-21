"""Lambda handler for AI Gateway cost attribution."""

from __future__ import annotations

import base64
import gzip
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import boto3
from pydantic import ValidationError

from cost_attribution.models import HandlerResponse, LogRecord, MetricResult
from cost_attribution.pricing import get_cache_savings, get_cost

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

cloudwatch = boto3.client("cloudwatch", region_name=os.environ.get("AWS_REGION", "us-east-1"))
dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))


# ── JWT claim extraction ─────────────────────────────────────────────────────


def _decode_jwt_claims(jwt_token: str) -> dict[str, Any]:
    """Decode JWT payload (base64 only, no verification — ALB already verified)."""
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

    cache_savings = get_cache_savings(
        provider,
        model,
        record.usage.cache_read_input_tokens,
        record.usage.cache_creation_input_tokens,
    )

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
    for i in range(0, len(metric_data), 1000):
        cloudwatch.put_metric_data(Namespace=METRIC_NAMESPACE, MetricData=metric_data[i : i + 1000])
    logger.info("Published %d metric data points for %d requests", len(metric_data), len(metrics))


# ── DynamoDB usage accumulation ──────────────────────────────────────────────


def _accumulate_usage(metrics: list[MetricResult]) -> None:
    """Write usage increments to DynamoDB (best-effort).

    Writes both team-level (``USAGE#TEAM#<team>``) and user-level
    (``USAGE#USER#<user>``) rows using atomic ``ADD`` operations so
    concurrent Lambda invocations never lose counts.
    """
    if not metrics:
        return

    table = dynamodb.Table(USAGE_TABLE)
    period = datetime.now(tz=UTC).strftime("%Y-%m")

    # Aggregate by (team, user) to minimise DynamoDB round-trips
    team_agg: dict[str, dict[str, int | float]] = {}
    user_agg: dict[str, dict[str, int | float]] = {}

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

    def _update(pk: str, sk: str, vals: dict[str, int | float]) -> None:
        from decimal import Decimal  # noqa: PLC0415

        table.update_item(
            Key={"pk": pk, "sk": sk},
            UpdateExpression=(
                "ADD total_tokens :tt, input_tokens :it, output_tokens :ot, "
                "cached_tokens :ct, total_cost_usd :tc, request_count :rc"
            ),
            ExpressionAttributeValues={
                ":tt": vals["total_tokens"],
                ":it": vals["input_tokens"],
                ":ot": vals["output_tokens"],
                ":ct": vals["cached_tokens"],
                ":tc": Decimal(str(round(vals["total_cost_usd"], 10))),
                ":rc": vals["request_count"],
            },
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

        # Best-effort DynamoDB accumulation — never blocks the main flow
        try:
            _accumulate_usage(extracted)
        except Exception:
            logger.warning("Failed to accumulate usage in DynamoDB", exc_info=True)

    return HandlerResponse(
        statusCode=200,
        total_events=len(log_events),
        processed=len(extracted),
        skipped=len(log_events) - len(extracted) - errors,
        errors=errors,
    ).model_dump(exclude_none=True)
