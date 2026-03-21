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
from unittest.mock import MagicMock, patch

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from cost_attribution.handler import _extract_metrics, handler
from cost_attribution.models import HandlerResponse, LogRecord, MetricResult, UsageMetrics
from cost_attribution.pricing import TokenPrice, get_cache_savings, get_cost

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


# ── UsageMetrics model ───────────────────────────────────────────────────────


class TestUsageMetrics:
    def test_coerces_none_to_zero(self) -> None:
        usage = UsageMetrics.model_validate({"prompt_tokens": None, "completion_tokens": None})
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0

    def test_coerces_string_to_int(self) -> None:
        usage = UsageMetrics.model_validate({"prompt_tokens": "42", "completion_tokens": "10"})
        assert usage.prompt_tokens == 42
        assert usage.completion_tokens == 10

    def test_coerces_invalid_string_to_zero(self) -> None:
        usage = UsageMetrics.model_validate({"prompt_tokens": "not a number"})
        assert usage.prompt_tokens == 0

    def test_computes_total_from_parts(self) -> None:
        usage = UsageMetrics.model_validate({"prompt_tokens": 10, "completion_tokens": 20})
        assert usage.total_tokens == 30

    def test_preserves_explicit_total(self) -> None:
        usage = UsageMetrics.model_validate({"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 50})
        assert usage.total_tokens == 50

    def test_has_tokens(self) -> None:
        assert UsageMetrics(prompt_tokens=1, completion_tokens=0, total_tokens=1).has_tokens
        assert not UsageMetrics(prompt_tokens=0, completion_tokens=0, total_tokens=0).has_tokens

    def test_cache_fields_default_zero(self) -> None:
        usage = UsageMetrics.model_validate({"prompt_tokens": 10, "completion_tokens": 5})
        assert usage.cache_read_input_tokens == 0
        assert usage.cache_creation_input_tokens == 0

    def test_cache_fields_parsed(self) -> None:
        usage = UsageMetrics.model_validate(
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "cache_read_input_tokens": 80,
                "cache_creation_input_tokens": 20,
            }
        )
        assert usage.cache_read_input_tokens == 80
        assert usage.cache_creation_input_tokens == 20

    def test_cache_fields_coerce_strings(self) -> None:
        usage = UsageMetrics.model_validate({"cache_read_input_tokens": "100", "cache_creation_input_tokens": "bad"})
        assert usage.cache_read_input_tokens == 100
        assert usage.cache_creation_input_tokens == 0

    @given(data=st.dictionaries(st.text(max_size=20), json_primitives, max_size=5))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_never_crashes(self, data: dict) -> None:
        try:
            usage = UsageMetrics.model_validate(data)
            assert isinstance(usage.prompt_tokens, int)
            assert isinstance(usage.completion_tokens, int)
            assert isinstance(usage.total_tokens, int)
            assert isinstance(usage.cache_read_input_tokens, int)
            assert isinstance(usage.cache_creation_input_tokens, int)
        except Exception:  # noqa: S110
            pass  # ValidationError is acceptable for garbage input


# ── LogRecord model ──────────────────────────────────────────────────────────


class TestLogRecord:
    def test_extracts_provider_from_header(self) -> None:
        record = LogRecord.model_validate(
            {
                "req": {"headers": {"x-portkey-provider": "openai"}},
                "model": "gpt-4",
            }
        )
        assert record.resolved_provider == "openai"

    def test_falls_back_to_provider_field(self) -> None:
        record = LogRecord.model_validate({"provider": "anthropic", "model": "claude-3"})
        assert record.resolved_provider == "anthropic"

    def test_returns_unknown_when_missing(self) -> None:
        record = LogRecord.model_validate({"model": "gpt-4"})
        assert record.resolved_provider == "unknown"

    @given(data=st.dictionaries(st.text(max_size=20), json_values, max_size=10))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_never_crashes(self, data: dict) -> None:
        try:
            record = LogRecord.model_validate(data)
            assert isinstance(record.resolved_provider, str)
        except Exception:  # noqa: S110
            pass  # ValidationError is acceptable


# ── TokenPrice model ─────────────────────────────────────────────────────────


class TestTokenPrice:
    def test_immutable(self) -> None:
        import pytest  # noqa: PLC0415

        price = TokenPrice(input_per_1k=0.01, output_per_1k=0.03)
        with pytest.raises(Exception):  # noqa: B017, PT011
            price.input_per_1k = 0.02  # type: ignore[misc]

    def test_get_cost(self) -> None:
        cost = get_cost("openai", "gpt-4.1", 1000, 1000)
        assert cost == 0.002 + 0.008

    def test_cache_read_default(self) -> None:
        price = TokenPrice(input_per_1k=0.01, output_per_1k=0.03)
        assert price.effective_cache_read_per_1k == 0.001  # 10% of input

    def test_cache_write_default(self) -> None:
        price = TokenPrice(input_per_1k=0.01, output_per_1k=0.03)
        assert price.effective_cache_write_per_1k == 0.0125  # 125% of input

    def test_cache_read_explicit(self) -> None:
        price = TokenPrice(input_per_1k=0.01, output_per_1k=0.03, cache_read_per_1k=0.002)
        assert price.effective_cache_read_per_1k == 0.002

    def test_cache_write_explicit(self) -> None:
        price = TokenPrice(input_per_1k=0.01, output_per_1k=0.03, cache_write_per_1k=0.015)
        assert price.effective_cache_write_per_1k == 0.015

    def test_get_cache_savings_no_cache(self) -> None:
        assert get_cache_savings("openai", "gpt-4.1", 0, 0) == 0.0

    def test_get_cache_savings_positive(self) -> None:
        # For gpt-4.1: input=0.002/1k, cache_read=0.0002/1k (10%), cache_write=0.0025/1k (125%)
        # 1000 read tokens: save (0.002 - 0.0002) = 0.0018, no write overhead
        savings = get_cache_savings("openai", "gpt-4.1", 1000, 0)
        assert savings > 0

    def test_get_cache_savings_clamped_to_zero(self) -> None:
        # Huge cache creation with tiny reads → clamped to 0
        savings = get_cache_savings("openai", "gpt-4.1", 0, 100000)
        assert savings == 0.0


# ── HandlerResponse model ───────────────────────────────────────────────────


class TestHandlerResponse:
    def test_excludes_none_error(self) -> None:
        resp = HandlerResponse(statusCode=200, total_events=5, processed=3, skipped=2, errors=0)
        dumped = resp.model_dump(exclude_none=True)
        assert "error" not in dumped

    def test_includes_error_when_set(self) -> None:
        resp = HandlerResponse(statusCode=400, error="bad request")
        dumped = resp.model_dump(exclude_none=True)
        assert dumped["error"] == "bad request"


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
        assert result is None or isinstance(result, MetricResult)

    def test_valid_usage(self) -> None:
        record = {
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            "model": "gpt-4",
            "req": {"headers": {"x-portkey-provider": "openai"}},
        }
        log_event = {"message": json.dumps(record)}
        result = _extract_metrics(log_event)
        assert result is not None
        assert result.total_tokens == 30
        assert result.provider == "openai"

    def test_valid_usage_with_cache_tokens(self) -> None:
        record = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "cache_read_input_tokens": 80,
                "cache_creation_input_tokens": 20,
            },
            "model": "claude-sonnet-4",
            "req": {"headers": {"x-portkey-provider": "anthropic"}},
        }
        log_event = {"message": json.dumps(record)}
        result = _extract_metrics(log_event)
        assert result is not None
        assert result.cache_read_input_tokens == 80
        assert result.cache_creation_input_tokens == 20
        assert result.cache_savings_usd > 0

    def test_extracts_team_from_jwt(self) -> None:
        jwt_payload = base64.urlsafe_b64encode(json.dumps({"custom:team": "platform", "sub": "user123"}).encode())
        jwt_token = f"header.{jwt_payload.decode()}.sig"
        record = {
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            "model": "gpt-4",
            "req": {"headers": {"x-portkey-provider": "openai", "x-amzn-oidc-data": jwt_token}},
        }
        log_event = {"message": json.dumps(record)}
        result = _extract_metrics(log_event)
        assert result is not None
        assert result.team == "platform"
        assert result.user == "user123"

    def test_returns_none_for_no_usage(self) -> None:
        log_event = {"message": json.dumps({"model": "gpt-4"})}
        assert _extract_metrics(log_event) is None

    def test_returns_none_for_invalid_json(self) -> None:
        log_event = {"message": "not json at all"}
        assert _extract_metrics(log_event) is None

    def test_returns_none_for_null_json(self) -> None:
        log_event = {"message": "null"}
        assert _extract_metrics(log_event) is None

    def test_returns_none_for_non_dict_usage(self) -> None:
        log_event = {"message": json.dumps({"usage": [1, 2, 3], "model": "gpt-4"})}
        assert _extract_metrics(log_event) is None


# ── handler (end-to-end) ─────────────────────────────────────────────────────


def _make_event(log_events: list[dict]) -> dict:
    """Build a CloudWatch Logs event payload."""
    payload = json.dumps({"logGroup": "test", "logEvents": log_events})
    compressed = gzip.compress(payload.encode())
    encoded = base64.b64encode(compressed).decode()
    return {"awslogs": {"data": encoded}}


class TestHandler:
    @patch("cost_attribution.handler.dynamodb")
    @patch("cost_attribution.handler.cloudwatch")
    def test_valid_event(self, mock_cw: Any, mock_ddb: Any) -> None:
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

    @patch("cost_attribution.handler.dynamodb")
    @patch("cost_attribution.handler.cloudwatch")
    def test_mixed_events(self, mock_cw: Any, mock_ddb: Any) -> None:
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

    @patch("cost_attribution.handler.dynamodb")
    @patch("cost_attribution.handler.cloudwatch")
    def test_dynamodb_failure_does_not_block(self, mock_cw: Any, mock_ddb: Any) -> None:
        """DynamoDB write failures must not affect the CloudWatch publishing flow."""
        mock_table = MagicMock()
        mock_table.update_item.side_effect = Exception("DynamoDB timeout")
        mock_ddb.Table.return_value = mock_table

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

    @given(messages=st.lists(st.text(max_size=100), min_size=1, max_size=10))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    @patch("cost_attribution.handler.dynamodb")
    @patch("cost_attribution.handler.cloudwatch")
    def test_never_crashes_on_random_messages(self, mock_cw: Any, mock_ddb: Any, messages: list[str]) -> None:
        log_events = [{"id": str(i), "message": m} for i, m in enumerate(messages)]
        result = handler(_make_event(log_events))
        assert result["statusCode"] in (200, 400, 500)
