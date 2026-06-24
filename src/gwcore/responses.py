"""Response envelope, error mapping, and cursor pagination.

One consistent contract for every control-plane handler:

- ``ok(body, ...)`` — 2xx success, optional ETag + cache headers.
- ``error_response(exc)`` — maps a ``ControlPlaneError`` to its HTTP status
  and error envelope.
- ``page(items, last_key)`` / ``parse_cursor(...)`` — opaque-cursor pagination
  over DynamoDB ``LastEvaluatedKey`` (O(1), never offset).

The wire shape matches the existing ``_build_response`` (statusCode / headers /
body) so it is a drop-in for API Gateway proxy and Function URL integrations.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Any

from gwcore.errors import ControlPlaneError, ValidationFailedError

_JSON_HEADERS = {"Content-Type": "application/json"}


def _dumps(body: Any) -> str:
    """Deterministic JSON serialization (sorted keys → stable ETags)."""
    return json.dumps(body, default=str, sort_keys=True, separators=(",", ":"))


def etag_for(body: Any) -> str:
    """Compute a strong ETag (sha256 of the canonical JSON) for ``body``."""
    digest = hashlib.sha256(_dumps(body).encode("utf-8")).hexdigest()
    return f'"{digest[:32]}"'


def ok(  # noqa: PLR0913 — keyword-only response options; all optional with defaults
    body: Any,
    *,
    status: int = 200,
    cache_seconds: int | None = None,
    etag: bool = False,
    if_none_match: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a success response.

    Args:
        body: JSON-serializable payload (dict, list, or Pydantic ``model_dump``).
        status: HTTP status (default 200).
        cache_seconds: when set, adds ``Cache-Control: private, max-age=N``.
        etag: when True, computes an ETag and, if ``if_none_match`` matches,
            returns ``304 Not Modified`` with no body.
        if_none_match: the request's ``If-None-Match`` header value.
        extra_headers: additional response headers.
    """
    headers = dict(_JSON_HEADERS)
    if cache_seconds is not None:
        headers["Cache-Control"] = f"private, max-age={cache_seconds}"
    if extra_headers:
        headers.update(extra_headers)

    if etag:
        tag = etag_for(body)
        headers["ETag"] = tag
        if if_none_match is not None and if_none_match == tag:
            return {"statusCode": 304, "headers": headers, "body": ""}

    return {"statusCode": status, "headers": headers, "body": _dumps(body)}


def error_response(exc: ControlPlaneError) -> dict[str, Any]:
    """Map a ``ControlPlaneError`` to an HTTP response with the error envelope."""
    return {
        "statusCode": exc.status,
        "headers": dict(_JSON_HEADERS),
        "body": _dumps(exc.to_body()),
    }


# ── Cursor pagination ─────────────────────────────────────────────────────────


def encode_cursor(last_key: dict[str, Any] | None) -> str | None:
    """Encode a DynamoDB ``LastEvaluatedKey`` into an opaque base64 cursor."""
    if not last_key:
        return None
    raw = _dumps(last_key).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def parse_cursor(cursor: str | None) -> dict[str, Any] | None:
    """Decode an opaque cursor back into a DynamoDB ``ExclusiveStartKey``.

    Raises:
        ValidationFailedError: if the cursor is present but malformed.
    """
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        decoded: Any = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        msg = "Malformed pagination cursor"
        raise ValidationFailedError(msg, details={"cursor": "invalid"}) from exc
    if not isinstance(decoded, dict):
        msg = "Malformed pagination cursor"
        raise ValidationFailedError(msg, details={"cursor": "invalid"})
    return decoded


def page(
    items: list[Any],
    last_key: dict[str, Any] | None = None,
    *,
    cache_seconds: int | None = None,
) -> dict[str, Any]:
    """Build a paginated list response: ``{items, next_cursor, count}``."""
    body = {
        "items": items,
        "count": len(items),
        "next_cursor": encode_cursor(last_key),
    }
    return ok(body, cache_seconds=cache_seconds)
