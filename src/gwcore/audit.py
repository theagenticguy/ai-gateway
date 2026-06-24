"""Append-only audit trail for control-plane mutations and authz decisions.

Every mutating call and every authorization decision (allow *and* deny) emits a
structured ``AuditEvent`` to a Kinesis Firehose stream, which lands it in Apache
Iceberg on S3 Tables for ACID + compaction + Athena queryability (ADR-016).

Emission is best-effort: a Firehose failure is logged and swallowed, never
failing the request. The Firehose client is created lazily and module-scoped for
warm-Lambda reuse.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import boto3

from gwcore.logging import get_logger

logger = get_logger("gwcore.audit")

_AUDIT_STREAM_ENV = "AUDIT_FIREHOSE_STREAM"
_firehose_client: Any = None


def _client() -> Any:
    """Lazily create and cache the Firehose client (warm reuse)."""
    global _firehose_client  # noqa: PLW0603 — module-scoped client cache for warm Lambda
    if _firehose_client is None:
        _firehose_client = boto3.client("firehose")
    return _firehose_client


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class AuditEvent:
    """A single audit record. Field order matches the Iceberg table schema."""

    action: str
    actor: str
    resource: str
    decision: str = "allow"  # allow | deny
    status: int = 200
    team: str = ""
    source_ip: str = ""
    correlation_id: str = ""
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    detail: str = ""
    ts: str = field(default_factory=_now_iso)

    def to_record(self) -> dict[str, Any]:
        """Render the event as a JSON-serializable dict for Firehose."""
        return asdict(self)


def emit(event: AuditEvent, *, stream_name: str | None = None) -> bool:
    """Write an ``AuditEvent`` to Firehose. Best-effort; never raises.

    Returns True on a successful put, False otherwise. If no stream is
    configured (``AUDIT_FIREHOSE_STREAM`` unset and no override), the event is
    logged at INFO and dropped — so local/test runs need no Firehose.
    """
    stream = stream_name or os.environ.get(_AUDIT_STREAM_ENV, "")
    record = event.to_record()
    if not stream:
        logger.info("audit (no stream configured)", extra={"fields": {"audit": record}})
        return False
    try:
        _client().put_record(
            DeliveryStreamName=stream,
            Record={"Data": (json.dumps(record, default=str) + "\n").encode("utf-8")},
        )
    except Exception:
        # Best-effort: an audit failure must never fail the request.
        logger.exception("Failed to emit audit event", extra={"fields": {"action": event.action}})
        return False
    return True


def event_from_request(  # noqa: PLR0913 — keyword-only audit fields; all but action/actor/resource optional
    req_event: dict[str, Any],
    *,
    action: str,
    actor: str,
    resource: str,
    decision: str = "allow",
    status: int = 200,
    team: str = "",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    detail: str = "",
) -> AuditEvent:
    """Build an ``AuditEvent``, pulling source IP + correlation id from the event."""
    rc = req_event.get("requestContext") or {}
    identity = rc.get("identity") or {}
    http = rc.get("http") or {}
    source_ip = str(identity.get("sourceIp", http.get("sourceIp", "")))
    correlation = str(rc.get("requestId", rc.get("request_id", "")))
    return AuditEvent(
        action=action,
        actor=actor,
        resource=resource,
        decision=decision,
        status=status,
        team=team,
        source_ip=source_ip,
        correlation_id=correlation,
        before=before,
        after=after,
        detail=detail,
    )
