"""agentgateway guardrail-webhook contract helpers (ADR-017).

When the data plane is agentgateway instead of Portkey, the guardrail-webhook
contract differs. agentgateway POSTs ``{"body": {"messages": [...]}}`` to the
webhook and expects back an ``action`` object (``pass`` / ``mask`` / ``reject``),
forwarding matched request headers (so ``x-amzn-oidc-data`` arrives as a header).
Portkey instead POSTed a domain body (``jwt_token`` / ``content``) and read a
``{"verdict": bool}`` envelope.

These helpers let a single Lambda speak BOTH contracts. A handler calls
``detect_contract(body)`` to branch, ``parse_request(...)`` to pull messages +
forwarded identity out of an agentgateway call, and ``pass_action`` /
``reject_action`` / ``mask_action`` to shape the response agentgateway expects.

Reference: agentgateway ``crates/agentgateway/src/llm/policy/webhook.rs`` and the
config-schema reference captured in ADR-017. The Portkey path is retained
unchanged so rollback is a routing flip, not a code revert.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

# Rough chars-per-token heuristic for request-time token estimation. agentgateway
# does not forward a token count to the guardrail webhook, but the handler now
# receives the actual messages, so we estimate locally. 4 chars/token is the
# standard coarse approximation for English + code (good enough for a pre-request
# rate-limit gate; the authoritative count comes post-hoc from cost_attribution).
_CHARS_PER_TOKEN = 4


class Contract(StrEnum):
    """Which data-plane webhook contract an inbound request speaks."""

    AGENTGATEWAY = "agentgateway"
    PORTKEY = "portkey"


def detect_contract(parsed_body: Any) -> Contract:
    """Classify a parsed webhook body as agentgateway- or Portkey-shaped.

    agentgateway sends ``{"body": {"messages": [...]}}``. Anything else (a
    domain body with ``jwt_token`` / ``content`` / ``team_id`` at the top level)
    is the legacy Portkey shape.
    """
    if isinstance(parsed_body, dict):
        inner = parsed_body.get("body")
        if isinstance(inner, dict) and isinstance(inner.get("messages"), list):
            return Contract.AGENTGATEWAY
    return Contract.PORTKEY


def extract_messages(parsed_body: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the message list out of an agentgateway guardrail request body."""
    inner = parsed_body.get("body", {})
    messages = inner.get("messages", []) if isinstance(inner, dict) else []
    return [m for m in messages if isinstance(m, dict)]


def messages_to_text(messages: list[dict[str, Any]]) -> str:
    """Flatten message contents into a single string for content scanning.

    Handles both string content and the OpenAI content-part list shape
    (``[{"type": "text", "text": "..."}]``).
    """
    parts: list[str] = []
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(
                piece["text"] for piece in content if isinstance(piece, dict) and isinstance(piece.get("text"), str)
            )
    return "\n".join(parts)


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate input tokens from message text (coarse, request-time only)."""
    chars = len(messages_to_text(messages))
    return chars // _CHARS_PER_TOKEN


def header_lookup(event: dict[str, Any], name: str) -> str:
    """Case-insensitive header lookup from a Lambda Function URL event."""
    headers = event.get("headers", {})
    if not isinstance(headers, dict):
        return ""
    lname = name.lower()
    for key, value in headers.items():
        if key.lower() == lname:
            return str(value)
    return ""


# ── Response builders (agentgateway action envelope) ─────────────────────────


def _envelope(action: dict[str, Any]) -> dict[str, Any]:
    """Wrap an action in the HTTP 200 Lambda Function URL response.

    agentgateway reads ``{"action": {...}}`` from the body. The Lambda always
    returns HTTP 200; the action carries the decision (same 200-not-4xx rule the
    Portkey contract used, for the same reason: a 4xx reads as a hook failure).
    """
    import json  # noqa: PLC0415

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"action": action}),
    }


def pass_action() -> dict[str, Any]:
    """Allow the request through unchanged."""
    return _envelope({"pass": {}})


def reject_action(status_code: int, body: str, reason: str) -> dict[str, Any]:
    """Reject the request. ``body`` is returned to the client as the HTTP body."""
    return _envelope({"reject": {"status_code": status_code, "body": body, "reason": reason}})


def mask_action(messages: list[dict[str, Any]], reason: str = "content masked") -> dict[str, Any]:
    """Replace the prompt messages with masked/redacted versions."""
    return _envelope({"mask": {"body": {"messages": messages}, "reason": reason}})
