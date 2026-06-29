"""agentgateway guardrail-webhook contract helpers (ADR-017).

agentgateway POSTs ``{"body": {"messages": [...]}}`` to the guardrail webhook
and expects back an ``action`` object (``pass`` / ``mask`` / ``reject``),
forwarding matched request headers (so ``x-amzn-oidc-data`` arrives as a header).

These helpers let a Lambda speak that contract: ``extract_messages`` /
``messages_to_text`` / ``estimate_tokens`` pull the prompt and a token estimate
out of an agentgateway call, ``header_lookup`` reads forwarded identity headers,
and ``pass_action`` / ``reject_action`` / ``mask_action`` shape the response
agentgateway expects.

Reference: agentgateway ``crates/agentgateway/src/llm/policy/webhook.rs`` and the
config-schema reference captured in ADR-017.
"""

from __future__ import annotations

from typing import Any

# Rough chars-per-token heuristic for request-time token estimation. agentgateway
# does not forward a token count to the guardrail webhook, but the handler now
# receives the actual messages, so we estimate locally. 4 chars/token is the
# standard coarse approximation for English + code (good enough for a pre-request
# rate-limit gate; the authoritative count comes post-hoc from cost_attribution).
_CHARS_PER_TOKEN = 4


def extract_messages(parsed_body: Any) -> list[dict[str, Any]]:
    """Pull the message list out of an agentgateway guardrail request body.

    Tolerant of malformed input (non-dict bodies, missing keys): returns an
    empty list rather than raising, so the caller degrades gracefully.
    """
    inner = parsed_body.get("body", {}) if isinstance(parsed_body, dict) else {}
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
    returns HTTP 200; the action carries the decision. A 4xx would read as a
    hook *failure* rather than a deny, so the decision must ride in the body.
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
