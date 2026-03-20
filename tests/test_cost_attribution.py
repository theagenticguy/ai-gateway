"""Property-based fuzz tests for the cost attribution handler.

Uses hypothesis to generate random inputs and verify the handler
never crashes on any input shape — only raises controlled exceptions
or returns valid results.
"""

from __future__ import annotations

import base64
import gzip
import json
from typing import Any
from unittest.mock import patch

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from cost_attribution.handler import (
    _extract_metrics,
    _extract_provider,
    _safe_int,
    handler,
)

# ── Strategies ───────────────────────────────────────────────────────────────

json_primitives = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=50),
)

json_values = st.recursive(
    json_primitives,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(max_size=20), children, max_size=5),
    ),
    max_leaves=20,
)


# ── _safe_int ────────────────────────────────────────────────────────────────


class TestSafeInt:
    @given(value=json_values)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_never_crashes(self, value: Any) -> None:
        result = _safe_int(value)
        assert isinstance(result, int)

    def test_none_returns_zero(self) -> None:
        assert _safe_int(None) == 0

    def test_valid_int(self) -> None:
        assert _safe_int(42) == 42
        assert _safe_int("100") == 100

    def test_invalid_returns_zero(self) -> None:
        assert _safe_int("not a number") == 0
        assert _safe_int([1, 2]) == 0
        assert _safe_int({"a": 1}) == 0


# ── _extract_provider ────────────────────────────────────────────────────────


class TestExtractProvider:
    @given(record=st.dictionaries(st.text(max_size=20), json_values, max_size=10))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_never_crashes(self, record: dict) -> None:
        result = _extract_provider(record)
        assert isinstance(result, str)

    def test_extracts_from_header(self) -> None:
        record = {"req": {"headers": {"x-portkey-provider": "openai"}}}
        assert _extract_provider(record) == "openai"

    def test_falls_back_to_provider_field(self) -> None:
        record = {"provider": "anthropic"}
        assert _extract_provider(record) == "anthropic"

    def test_returns_unknown_when_missing(self) -> None:
        assert _extract_provider({}) == "unknown"

    def test_non_string_provider_returns_unknown(self) -> None:
        assert _extract_provider({"provider": 123}) == "unknown"
        assert _extract_provider({"provider": ["a"]}) == "unknown"


# ── _extract_metrics ─────────────────────────────────────────────────────────


class TestExtractMetrics:
    @given(
        log_event=st.fixed_dictionaries(
            {"message": st.text(max_size=200)},
            optional={"id": st.text(max_size=20)},
        )
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_never_crashes_on_text_message(self, log_event: dict) -> None:
        result = _extract_metrics(log_event)
        assert result is None or isinstance(result, dict)

    @given(
        usage=st.fixed_dictionaries(
            {},
            optional={
                "prompt_tokens": json_primitives,
                "completion_tokens": json_primitives,
                "total_tokens": json_primitives,
            },
        )
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_never_crashes_on_structured_usage(self, usage: dict) -> None:
        record = {"usage": usage, "model": "gpt-4"}
        log_event = {"message": json.dumps(record)}
        result = _extract_metrics(log_event)
        assert result is None or isinstance(result, dict)
        if result is not None:
            assert "provider" in result
            assert "model" in result
            assert "total_tokens" in result
            assert "cost_usd" in result

    def test_valid_usage(self) -> None:
        record = {
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            "model": "gpt-4",
            "req": {"headers": {"x-portkey-provider": "openai"}},
        }
        log_event = {"message": json.dumps(record)}
        result = _extract_metrics(log_event)
        assert result is not None
        assert result["total_tokens"] == 30
        assert result["provider"] == "openai"

    def test_returns_none_for_no_usage(self) -> None:
        log_event = {"message": json.dumps({"model": "gpt-4"})}
        assert _extract_metrics(log_event) is None

    def test_returns_none_for_invalid_json(self) -> None:
        log_event = {"message": "not json at all"}
        assert _extract_metrics(log_event) is None

    def test_returns_none_for_non_dict_usage(self) -> None:
        record = {"usage": [1, 2, 3], "model": "gpt-4"}
        log_event = {"message": json.dumps(record)}
        assert _extract_metrics(log_event) is None

        record2 = {"usage": "not a dict", "model": "gpt-4"}
        log_event2 = {"message": json.dumps(record2)}
        assert _extract_metrics(log_event2) is None


# ── handler (end-to-end) ─────────────────────────────────────────────────────


def _make_event(log_events: list[dict]) -> dict:
    """Build a CloudWatch Logs event payload."""
    payload = json.dumps({"logGroup": "test", "logEvents": log_events})
    compressed = gzip.compress(payload.encode())
    encoded = base64.b64encode(compressed).decode()
    return {"awslogs": {"data": encoded}}


class TestHandler:
    @patch("cost_attribution.handler.cloudwatch")
    def test_valid_event(self, mock_cw: Any) -> None:
        log_events = [
            {
                "id": "1",
                "message": json.dumps(
                    {
                        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                        "model": "gpt-4",
                        "req": {"headers": {"x-portkey-provider": "openai"}},
                    }
                ),
            }
        ]
        result = handler(_make_event(log_events))
        assert result["statusCode"] == 200
        assert result["processed"] == 1

    def test_invalid_payload(self) -> None:
        result = handler({"awslogs": {"data": "not-valid-base64!!!"}})
        assert result["statusCode"] == 400

    @patch("cost_attribution.handler.cloudwatch")
    def test_mixed_events(self, mock_cw: Any) -> None:
        log_events = [
            {"id": "1", "message": "garbage"},
            {
                "id": "2",
                "message": json.dumps(
                    {
                        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
                        "model": "claude-3",
                    }
                ),
            },
        ]
        result = handler(_make_event(log_events))
        assert result["statusCode"] == 200
        assert result["processed"] == 1
        assert result["skipped"] == 1

    @given(messages=st.lists(st.text(max_size=100), min_size=1, max_size=10))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    @patch("cost_attribution.handler.cloudwatch")
    def test_never_crashes_on_random_messages(self, mock_cw: Any, messages: list[str]) -> None:
        log_events = [{"id": str(i), "message": m} for i, m in enumerate(messages)]
        result = handler(_make_event(log_events))
        assert result["statusCode"] in (200, 400, 500)
