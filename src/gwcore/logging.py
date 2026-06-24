"""Structured JSON logging with a per-request correlation id.

One logger shape across the plane: every line is JSON with a ``correlation_id``
taken from the API Gateway request id, so a single request is greppable across
handler logs, audit events, and metrics. Mirrors the AWS Powertools log shape
so a later swap is mechanical (ADR-016).
"""

from __future__ import annotations

import json
import logging
from typing import Any

_CORRELATION_ID = "correlation_id"


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        cid = getattr(record, _CORRELATION_ID, None)
        if cid:
            payload[_CORRELATION_ID] = cid
        # Merge any structured extra fields attached via ``extra={"fields": {...}}``.
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def get_logger(name: str) -> logging.Logger:
    """Return a JSON logger, idempotently configured (safe on warm Lambda)."""
    logger = logging.getLogger(name)
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def correlation_id(event: dict[str, Any]) -> str:
    """Extract the API Gateway / Function URL request id for correlation."""
    rc = event.get("requestContext") or {}
    return str(rc.get("requestId", rc.get("request_id", "")))


def bind(logger: logging.Logger, cid: str) -> logging.LoggerAdapter[logging.Logger]:
    """Bind a correlation id so every line from the adapter carries it."""
    return logging.LoggerAdapter(logger, {_CORRELATION_ID: cid})
