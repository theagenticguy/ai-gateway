"""Lambda handler for the AI Gateway content scanner.

A Portkey plugin webhook (Function URL). Accepts POST requests with a JSON body
containing the content to scan, team ID, model, and request ID, and ALWAYS
returns HTTP 200 with a ``verdict`` field — a 4xx would be treated by Portkey as
a hook *failure*, not a block.

Scan failures are treated as *allow* (fail-open) — a broken scanner must never
block legitimate traffic. The handler does no authorization; it scans the
content carried in the request body, keyed by ``team_id``.

Migrated onto gwcore (ADR-016): structured JSON logging with a correlation id,
a latency Timer, scan-verdict metrics, and a deny-audit on every block. The
Portkey response contract and fail-open behavior are unchanged.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

import boto3
from pydantic import ValidationError

from content_scanner.models import (
    InjectionDetection,
    PiiDetection,
    ScanMode,
    ScannerAppConfig,
    ScanRequest,
    ScanResponse,
    TeamScanConfig,
)
from content_scanner.patterns import scan_injection
from content_scanner.pii import scan_pii
from gwcore import audit
from gwcore.logging import bind, correlation_id, get_logger
from gwcore.telemetry import Timer, emit_metric

logger = get_logger("content_scanner")

# ── Configuration ────────────────────────────────────────────────────────────

_CONFIG_TABLE = os.environ.get("CONFIG_TABLE_NAME", "")
_DEFAULT_PII_MODE = ScanMode(os.environ.get("DEFAULT_PII_MODE", "detect"))
_DEFAULT_INJECTION_MODE = ScanMode(os.environ.get("DEFAULT_INJECTION_MODE", "detect"))
APPCONFIG_PATH = os.environ.get("APPCONFIG_PATH", "")

_dynamodb = None


def _get_dynamodb_table():
    """Lazy-initialise the DynamoDB Table resource."""
    global _dynamodb  # noqa: PLW0603
    if _dynamodb is None and _CONFIG_TABLE:
        _dynamodb = boto3.resource("dynamodb").Table(_CONFIG_TABLE)
    return _dynamodb


def _load_team_config(team_id: str) -> TeamScanConfig:
    """Load per-team scanning config from DynamoDB, falling back to env defaults."""
    table = _get_dynamodb_table()
    if table is not None:
        try:
            resp = table.get_item(Key={"team_id": team_id})
            item = resp.get("Item")
            if item:
                return TeamScanConfig.model_validate(item)
        except Exception:
            logger.warning("Failed to load config for team %s, using defaults", team_id, exc_info=True)

    return TeamScanConfig(
        team_id=team_id,
        pii_mode=_DEFAULT_PII_MODE,
        injection_mode=_DEFAULT_INJECTION_MODE,
    )


def _load_appconfig() -> ScannerAppConfig:
    """Read scanner feature flags from the AppConfig Lambda extension (localhost:2772).

    The extension is configured via the ``AWS_APPCONFIG_EXTENSION_PREFETCH_LIST``
    environment variable and keeps a local cache with a 45-second poll interval.
    On any failure the scanner defaults to **enabled** (fail-open).
    """
    if not APPCONFIG_PATH:
        return ScannerAppConfig()  # default: enabled=True
    try:
        # URL is localhost:2772 (AppConfig Lambda extension) — not user-controlled
        url = f"http://localhost:2772{APPCONFIG_PATH}"  # nosemgrep
        req = urllib.request.Request(url)  # noqa: S310
        with urllib.request.urlopen(req, timeout=1) as resp:  # noqa: S310  # nosemgrep
            data = json.loads(resp.read())
            return ScannerAppConfig.model_validate(data)
    except Exception:
        logger.warning("Failed to read AppConfig, defaulting to enabled", exc_info=True)
        return ScannerAppConfig()  # fail-open


# ── Portkey response format ──────────────────────────────────────────────────


def _build_portkey_response(verdict: bool, scan_response: ScanResponse) -> dict[str, Any]:
    """Build a Portkey ``PluginHandlerResponse``-shaped Lambda response.

    *verdict* is the Portkey boolean: ``True`` = allow the request through,
    ``False`` = block/intercept.  The detailed scanner verdict string, detections,
    and optional transformed data live inside ``data``.
    """
    data: dict[str, Any] = {
        "request_id": scan_response.request_id,
        "verdict_reason": scan_response.verdict,
        "detections": [d.model_dump(mode="json") for d in scan_response.detections] if scan_response.detections else [],
    }
    if scan_response.content and scan_response.verdict == "redact":
        data["transformedData"] = {"request": {"json": {"messages": [{"content": scan_response.content}]}}}

    portkey_body: dict[str, Any] = {"verdict": verdict, "data": data}
    if scan_response.error:
        portkey_body["error"] = scan_response.error

    return {
        "statusCode": 200,  # Always 200 for Portkey
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(portkey_body),
    }


# ── Handler ──────────────────────────────────────────────────────────────────


def _audit_block(event: dict[str, Any], request: ScanRequest, scan_response: ScanResponse) -> None:
    """Emit a deny metric + audit event for a blocked request (best-effort).

    The audit detail records *what kind* of detection fired and how many, never
    the matched text — PII / injection payloads must never land in the audit
    trail. PII detections carry an ``entity_type``; injection detections carry a
    ``pattern_name``.
    """
    categories: dict[str, int] = {}
    for d in scan_response.detections:
        key = d.entity_type if isinstance(d, PiiDetection) else d.pattern_name
        categories[key] = categories.get(key, 0) + 1
    detail = ",".join(f"{k}={v}" for k, v in sorted(categories.items()))
    emit_metric("ContentBlocked", 1, dimensions={"Route": "content_scanner"})
    audit.emit(
        audit.event_from_request(
            event,
            action="content.scan",
            actor=request.team_id,
            resource=request.model or request.request_id,
            decision="deny",
            status=200,
            team=request.team_id,
            detail=detail,
        )
    )


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Lambda Function URL handler for content scanning.

    Returns a Portkey ``PluginHandlerResponse`` (always HTTP 200).
    On any internal error the scanner fails open (verdict=True / allow).
    """
    cid = correlation_id(event)
    log = bind(logger, cid)

    with Timer("RequestLatency", route="content_scanner"):
        try:
            body = event.get("body", "")
            if isinstance(body, str):
                body = json.loads(body)
            request = ScanRequest.model_validate(body)
        except json.JSONDecodeError:
            # Never echo the exception text — a JSON error can quote the payload,
            # which may carry PII. Log/return a generic message only.
            log.warning("Invalid request body: malformed JSON")
            emit_metric("ContentScannerError", 1, dimensions={"Code": "bad_request"})
            return _build_portkey_response(
                True,
                ScanResponse(verdict="allow", error="Invalid request: malformed JSON"),
            )
        except ValidationError as exc:
            # A pydantic ValidationError repr can include the offending input
            # value (e.g. ``content``, which may be PII). Surface only the error
            # COUNT, never the field values.
            count = exc.error_count()
            log.warning("Invalid request body: %d validation error(s)", count)
            emit_metric("ContentScannerError", 1, dimensions={"Code": "bad_request"})
            return _build_portkey_response(
                True,
                ScanResponse(verdict="allow", error=f"Invalid request: {count} validation error(s)"),
            )

        # ── AppConfig kill-switch ─────────────────────────────────────────────
        app_config = _load_appconfig()
        if not app_config.enabled:
            log.info("Scanner globally disabled via AppConfig")
            return _build_portkey_response(True, ScanResponse(verdict="allow", request_id=request.request_id))

        team_enabled = app_config.team_overrides.get(request.team_id)
        if team_enabled is False:
            log.info("Scanner disabled for team %s via AppConfig", request.team_id)
            return _build_portkey_response(True, ScanResponse(verdict="allow", request_id=request.request_id))

        # ── Run scans ─────────────────────────────────────────────────────────
        try:
            return _scan(request, event)
        except Exception:
            # Scan failure = allow (fail-open). Never block on scanner errors.
            log.exception("Scan failed for request %s, failing open", request.request_id)
            emit_metric("ContentScannerError", 1, dimensions={"Code": "scan_error"})
            return _build_portkey_response(
                True,
                ScanResponse(
                    verdict="allow",
                    request_id=request.request_id,
                    content=request.content,
                    error="Internal scan error — request allowed",
                ),
            )


def _scan(request: ScanRequest, event: dict[str, Any] | None = None) -> dict[str, Any]:
    """Orchestrate PII + injection scans and determine verdict."""
    config = _load_team_config(request.team_id)

    all_detections: list[PiiDetection | InjectionDetection] = []
    final_content = request.content
    verdict = "allow"

    # ── PII scan ─────────────────────────────────────────────────────────
    if config.pii_mode != ScanMode.off:
        pii_result = scan_pii(
            request.content,
            mode=config.pii_mode,
            allowed_types=frozenset(config.pii_types_to_scan),
            score_threshold=config.pii_score_threshold,
        )
        all_detections.extend(pii_result.pii_detections)

        if pii_result.detected:
            if config.pii_mode == ScanMode.block:
                verdict = "block"
            elif config.pii_mode == ScanMode.redact and pii_result.redacted_content:
                verdict = "redact"
                final_content = pii_result.redacted_content

    # ── Injection scan ───────────────────────────────────────────────────
    if config.injection_mode != ScanMode.off:
        inj_result = scan_injection(request.content, mode=config.injection_mode)
        all_detections.extend(inj_result.injection_detections)

        if inj_result.detected:
            if config.injection_mode == ScanMode.block:
                verdict = "block"
            elif config.injection_mode in (ScanMode.detect, ScanMode.redact) and verdict != "block":
                # Injection detections are informational in detect/redact mode —
                # we don't modify content but escalate to block if critical.
                critical = any(
                    d.severity.value == "critical"
                    for d in inj_result.injection_detections
                    if isinstance(d, InjectionDetection)
                )
                if critical and config.injection_mode != ScanMode.detect:
                    verdict = "block"

    portkey_verdict = verdict != "block"
    scan_response = ScanResponse(
        verdict=verdict,  # type: ignore[arg-type]
        request_id=request.request_id,
        content=final_content,
        detections=all_detections,
    )
    if not portkey_verdict and event is not None:
        _audit_block(event, request, scan_response)
    return _build_portkey_response(portkey_verdict, scan_response)
