"""Lambda handler for AI Gateway cost attribution."""

from __future__ import annotations

import base64
import gzip
import json
import logging
import os
from typing import Any

import boto3

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
cloudwatch = boto3.client("cloudwatch")


def _decode_log_data(event: dict[str, Any]) -> dict[str, Any]:
    return json.loads(gzip.decompress(base64.b64decode(event["awslogs"]["data"])))


def _safe_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def _extract_provider(record: dict[str, Any]) -> str:
    req = record.get("req", {})
    headers = req.get("headers", {}) if isinstance(req, dict) else {}
    provider = headers.get("x-portkey-provider", "")
    return provider or record.get("provider", "unknown")


def _extract_metrics(log_event: dict[str, Any]) -> dict[str, Any] | None:
    try:
        message = log_event.get("message", "")
        record = json.loads(message) if isinstance(message, str) else message
    except (json.JSONDecodeError, TypeError):
        return None
    usage = record.get("usage", {})
    if not usage:
        return None
    prompt_tokens = _safe_int(usage.get("prompt_tokens"))
    completion_tokens = _safe_int(usage.get("completion_tokens"))
    total_tokens = _safe_int(usage.get("total_tokens"))
    if total_tokens == 0 and (prompt_tokens + completion_tokens) == 0:
        return None
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens
    provider = _extract_provider(record)
    model = record.get("model", "unknown")
    return {
        "provider": provider,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost_usd": get_cost(provider, model, prompt_tokens, completion_tokens),
    }


def _publish_metrics(metrics: list[dict[str, Any]]) -> None:
    if not metrics:
        return
    metric_data: list[dict[str, Any]] = []
    for m in metrics:
        dims = [{"Name": "Provider", "Value": m["provider"]}, {"Name": "Model", "Value": m["model"]}]
        metric_data.extend(
            [
                {"MetricName": "TokensUsed", "Dimensions": dims, "Value": float(m["total_tokens"]), "Unit": "Count"},
                {"MetricName": "EstimatedCostUsd", "Dimensions": dims, "Value": m["cost_usd"], "Unit": "None"},
                {"MetricName": "RequestCount", "Dimensions": dims, "Value": 1.0, "Unit": "Count"},
            ]
        )
    for i in range(0, len(metric_data), 1000):
        cloudwatch.put_metric_data(Namespace=METRIC_NAMESPACE, MetricData=metric_data[i : i + 1000])
    logger.info("Published %d metric data points for %d requests", len(metric_data), len(metrics))


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        log_data = _decode_log_data(event)
    except Exception:
        logger.exception("Failed to decode log event payload")
        return {"statusCode": 400, "error": "Failed to decode log data"}
    log_events = log_data.get("logEvents", [])
    logger.info("Processing %d log events from log group %s", len(log_events), log_data.get("logGroup", "unknown"))
    extracted: list[dict[str, Any]] = []
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
            return {
                "statusCode": 500,
                "processed": len(extracted),
                "errors": errors,
                "error": "Failed to publish metrics",
            }
    return {
        "statusCode": 200,
        "total_events": len(log_events),
        "processed": len(extracted),
        "skipped": len(log_events) - len(extracted) - errors,
        "errors": errors,
    }
