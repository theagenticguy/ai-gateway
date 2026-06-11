"""Property-based fuzz tests for the cost attribution handler.

Uses hypothesis to generate random inputs and verify the handler
never crashes on any input shape -- only raises controlled exceptions
or returns valid results.

Also covers E.5 (model-level usage tracking) and E.6 (budget alerts).
"""

from __future__ import annotations

import base64
import gzip
import json
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from cost_attribution.handler import (
    _extract_metrics,
    _find_top_model,
    check_and_publish_alerts,
    detect_crossed_thresholds,
    handler,
)
from cost_attribution.models import (
    HandlerResponse,
    LogRecord,
    MetricResult,
    ModelLimit,
    UsageMetrics,
)
from cost_attribution.pricing import TokenPrice, get_cache_savings, get_cost, is_known_model

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
        import pytest

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
        # Huge cache creation with tiny reads -> clamped to 0
        savings = get_cache_savings("openai", "gpt-4.1", 0, 100000)
        assert savings == 0.0

    def test_openai_on_bedrock_rows_present(self) -> None:
        # The Codex / gpt-oss lane models must have explicit pricing rows.
        for model in ("openai.gpt-5.5", "openai.gpt-5.4", "openai.gpt-oss-120b", "openai.gpt-oss-20b"):
            assert is_known_model("bedrock", model), model

    def test_gpt_oss_has_no_cache_lane(self) -> None:
        # gpt-oss on Bedrock has no cached-token billing lane: savings must be 0,
        # NOT the 10%-of-input default a None cache_read would otherwise produce.
        assert get_cache_savings("bedrock", "openai.gpt-oss-120b", 5000, 0) == 0.0
        assert get_cache_savings("bedrock", "openai.gpt-oss-20b", 5000, 1000) == 0.0
        # cost still computes from the explicit (verified) row
        assert get_cost("bedrock", "openai.gpt-oss-120b", 1000, 1000) == 0.00015 + 0.0006

    def test_gpt_5_5_verified_pricing_and_cache(self) -> None:
        # Verified AWS Bedrock pricing-page rates (per 1K) + 10x cache-read discount.
        assert get_cost("bedrock", "openai.gpt-5.5", 1000, 1000) == 0.0055 + 0.033
        # GPT-5.5 DOES have a cache lane -> positive savings on cache reads.
        assert get_cache_savings("bedrock", "openai.gpt-5.5", 5000, 0) > 0

    def test_cache_supported_default_true(self) -> None:
        # Back-compat: models without the flag still cache (Claude/Nova unchanged).
        assert get_cache_savings("bedrock", "anthropic.claude-sonnet-4-20250514-v1:0", 5000, 0) > 0

    def test_is_known_model_false_for_unpriced(self) -> None:
        assert is_known_model("bedrock", "openai.gpt-does-not-exist") is False

    def test_unknown_model_warns_but_still_prices(self, caplog: Any) -> None:
        import logging

        with caplog.at_level(logging.WARNING, logger="cost_attribution.pricing"):
            cost = get_cost("bedrock", "openai.gpt-does-not-exist", 1000, 1000)
        assert cost == 0.01 + 0.03  # default-price estimate, still a number
        assert any("No pricing row" in r.message for r in caplog.records)


class TestUnverifiedIdentity:
    def _event(self, claims: dict[str, Any]) -> dict[str, Any]:
        token = "h." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".s"
        return {"req": {"headers": {"x-amzn-oidc-data": token}}}

    def test_identity_trusted_when_jwt_enforced(self) -> None:
        from cost_attribution.handler import _extract_identity

        ev = self._event({"custom:team": "platform", "sub": "u-123"})
        with patch.dict("os.environ", {"JWT_AUTH_ENFORCED": "true"}):
            assert _extract_identity(ev) == ("platform", "u-123")

    def test_identity_tagged_unverified_when_jwt_not_enforced(self) -> None:
        from cost_attribution.handler import _extract_identity

        ev = self._event({"custom:team": "platform", "sub": "u-123"})
        with patch.dict("os.environ", {"JWT_AUTH_ENFORCED": "false"}):
            assert _extract_identity(ev) == ("unverified-platform", "unverified-u-123")


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


# ── ModelLimit model (E.5) ───────────────────────────────────────────────────


class TestModelLimit:
    def test_model_limit_creation(self) -> None:
        ml = ModelLimit(monthly_usd=Decimal(200), daily_tokens=100000)
        assert ml.monthly_usd == Decimal(200)
        assert ml.daily_tokens == 100000

    def test_model_limit_frozen(self) -> None:
        import pytest

        ml = ModelLimit(monthly_usd=Decimal(200))
        with pytest.raises(Exception):  # noqa: B017, PT011
            ml.monthly_usd = Decimal(300)  # type: ignore[misc]

    def test_model_limit_defaults(self) -> None:
        ml = ModelLimit(monthly_usd=Decimal(0))
        assert ml.daily_tokens == -1


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
        # With JWT auth enforced at the ALB, the header is trusted as-is.
        with patch.dict("os.environ", {"JWT_AUTH_ENFORCED": "true"}):
            result = _extract_metrics(log_event)
        assert result is not None
        assert result.team == "platform"
        assert result.user == "user123"

    def test_jwt_identity_unverified_when_auth_off(self) -> None:
        # Without ALB JWT enforcement the header is spoofable -> tagged unverified.
        jwt_payload = base64.urlsafe_b64encode(json.dumps({"custom:team": "platform", "sub": "user123"}).encode())
        jwt_token = f"header.{jwt_payload.decode()}.sig"
        record = {
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            "model": "gpt-4",
            "req": {"headers": {"x-amzn-oidc-data": jwt_token}},
        }
        log_event = {"message": json.dumps(record)}
        with patch.dict("os.environ", {"JWT_AUTH_ENFORCED": "false"}):
            result = _extract_metrics(log_event)
        assert result is not None
        assert result.team == "unverified-platform"
        assert result.user == "unverified-user123"

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


# ── E.6: Alert threshold detection ──────────────────────────────────────────


class TestDetectCrossedThresholds:
    def test_no_thresholds_crossed(self) -> None:
        result = detect_crossed_thresholds(40.0, [50, 80, 100], [])
        assert result == []

    def test_fifty_pct_crossed(self) -> None:
        result = detect_crossed_thresholds(55.0, [50, 80, 100], [])
        assert result == [50]

    def test_multiple_thresholds_crossed(self) -> None:
        result = detect_crossed_thresholds(85.0, [50, 80, 100], [])
        assert result == [50, 80]

    def test_all_thresholds_crossed(self) -> None:
        result = detect_crossed_thresholds(110.0, [50, 80, 100], [])
        assert result == [50, 80, 100]

    def test_already_sent_excluded(self) -> None:
        result = detect_crossed_thresholds(85.0, [50, 80, 100], [50])
        assert result == [80]

    def test_all_already_sent(self) -> None:
        result = detect_crossed_thresholds(110.0, [50, 80, 100], [50, 80, 100])
        assert result == []

    def test_exact_threshold(self) -> None:
        result = detect_crossed_thresholds(50.0, [50, 80, 100], [])
        assert result == [50]

    def test_just_below_threshold(self) -> None:
        result = detect_crossed_thresholds(49.9, [50, 80, 100], [])
        assert result == []

    def test_empty_thresholds(self) -> None:
        result = detect_crossed_thresholds(90.0, [], [])
        assert result == []

    def test_custom_thresholds(self) -> None:
        result = detect_crossed_thresholds(75.0, [25, 50, 75, 90], [])
        assert result == [25, 50, 75]

    @given(
        utilization=st.floats(min_value=0, max_value=200, allow_nan=False, allow_infinity=False),
        thresholds=st.lists(st.integers(min_value=1, max_value=200), min_size=0, max_size=10),
        already_sent=st.lists(st.integers(min_value=1, max_value=200), min_size=0, max_size=10),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_detect_thresholds_never_crashes(
        self, utilization: float, thresholds: list[int], already_sent: list[int]
    ) -> None:
        """detect_crossed_thresholds should never crash on any reasonable input."""
        result = detect_crossed_thresholds(utilization, thresholds, already_sent)
        assert isinstance(result, list)
        # All returned thresholds should be in the original thresholds
        for t in result:
            assert t in thresholds
        # No returned threshold should be in already_sent
        for t in result:
            assert t not in already_sent
        # All returned thresholds should have been crossed
        for t in result:
            assert utilization >= t

    @given(
        utilization=st.floats(min_value=0, max_value=200, allow_nan=False, allow_infinity=False),
        thresholds=st.lists(st.integers(min_value=1, max_value=200), min_size=0, max_size=10, unique=True),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_idempotency(self, utilization: float, thresholds: list[int]) -> None:
        """Running with empty alerts_sent, then feeding results back, should yield no new alerts."""
        first_pass = detect_crossed_thresholds(utilization, thresholds, [])
        second_pass = detect_crossed_thresholds(utilization, thresholds, first_pass)
        assert second_pass == []

    @given(
        utilization=st.floats(min_value=0, max_value=200, allow_nan=False, allow_infinity=False),
        thresholds=st.lists(st.integers(min_value=1, max_value=200), min_size=0, max_size=10),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_results_always_sorted(self, utilization: float, thresholds: list[int]) -> None:
        result = detect_crossed_thresholds(utilization, thresholds, [])
        assert result == sorted(result)


def _metric(
    *,
    provider: str = "openai",
    model: str = "gpt-4",
    cost_usd: float = 0.5,
    team: str = "team-a",
    user: str = "u1",
) -> MetricResult:
    """Helper to build a MetricResult with sensible defaults."""
    return MetricResult(
        provider=provider,
        model=model,
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        cost_usd=cost_usd,
        team=team,
        user=user,
    )


class TestFindTopModel:
    def test_single_model(self) -> None:
        metrics = [_metric()]
        assert _find_top_model(metrics, "team-a") == "gpt-4"

    def test_multiple_models(self) -> None:
        metrics = [
            _metric(cost_usd=0.5),
            _metric(provider="anthropic", model="claude-opus-4", cost_usd=2.0),
            _metric(cost_usd=0.3),
        ]
        assert _find_top_model(metrics, "team-a") == "claude-opus-4"

    def test_filters_by_team(self) -> None:
        metrics = [
            _metric(cost_usd=5.0, team="team-b"),
            _metric(provider="anthropic", model="claude-opus-4", cost_usd=2.0),
        ]
        assert _find_top_model(metrics, "team-a") == "claude-opus-4"

    def test_no_metrics_for_team(self) -> None:
        metrics = [_metric(team="team-b")]
        assert _find_top_model(metrics, "team-a") == "unknown"


class TestCheckAndPublishAlerts:
    @patch("cost_attribution.handler.SNS_TOPIC_ARN", "")
    def test_no_topic_arn_noop(self) -> None:
        assert check_and_publish_alerts([_metric()]) == 0

    def test_empty_metrics_noop(self) -> None:
        assert check_and_publish_alerts([]) == 0

    @patch("cost_attribution.handler.sns")
    @patch("cost_attribution.handler.dynamodb")
    @patch("cost_attribution.handler.SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:budget-alerts")
    def test_publishes_alert_on_threshold_crossed(self, mock_ddb: Any, mock_sns: Any) -> None:
        """Alert should be published when usage crosses a threshold."""
        mock_budgets_table = MagicMock()
        mock_usage_table = MagicMock()

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router

        mock_budgets_table.get_item.return_value = {
            "Item": {
                "monthly_budget_usd": Decimal(1000),
                "alert_thresholds": [50, 80, 100],
                "alerts_sent": [],
            }
        }
        mock_usage_table.get_item.return_value = {
            "Item": {"total_cost_usd": Decimal(550)}  # 55% -> crosses 50
        }

        result = check_and_publish_alerts([_metric(cost_usd=10.0)])

        assert result == 1
        mock_sns.publish.assert_called_once()
        published_msg = json.loads(mock_sns.publish.call_args[1]["Message"])
        assert published_msg["team"] == "team-a"
        assert published_msg["threshold_pct"] == 50

    @patch("cost_attribution.handler.sns")
    @patch("cost_attribution.handler.dynamodb")
    @patch("cost_attribution.handler.SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:budget-alerts")
    def test_does_not_resend_alert(self, mock_ddb: Any, mock_sns: Any) -> None:
        """Already-sent thresholds should not trigger new alerts."""
        mock_budgets_table = MagicMock()
        mock_usage_table = MagicMock()

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router

        mock_budgets_table.get_item.return_value = {
            "Item": {
                "monthly_budget_usd": Decimal(1000),
                "alert_thresholds": [50, 80, 100],
                "alerts_sent": [50],  # Already sent
            }
        }
        mock_usage_table.get_item.return_value = {
            "Item": {"total_cost_usd": Decimal(550)}  # 55%
        }

        result = check_and_publish_alerts([_metric(cost_usd=10.0)])

        assert result == 0
        mock_sns.publish.assert_not_called()

    @patch("cost_attribution.handler.sns")
    @patch("cost_attribution.handler.dynamodb")
    @patch("cost_attribution.handler.SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:budget-alerts")
    def test_multiple_thresholds_crossed_at_once(self, mock_ddb: Any, mock_sns: Any) -> None:
        """Multiple thresholds crossed in one batch should publish multiple alerts."""
        mock_budgets_table = MagicMock()
        mock_usage_table = MagicMock()

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router

        mock_budgets_table.get_item.return_value = {
            "Item": {
                "monthly_budget_usd": Decimal(1000),
                "alert_thresholds": [50, 80, 100],
                "alerts_sent": [],
            }
        }
        mock_usage_table.get_item.return_value = {
            "Item": {"total_cost_usd": Decimal(850)}  # 85% -> crosses 50 and 80
        }

        result = check_and_publish_alerts([_metric(cost_usd=10.0)])

        assert result == 2
        assert mock_sns.publish.call_count == 2

    @patch("cost_attribution.handler.sns")
    @patch("cost_attribution.handler.dynamodb")
    @patch("cost_attribution.handler.SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:budget-alerts")
    def test_no_budget_record_no_alert(self, mock_ddb: Any, mock_sns: Any) -> None:
        """Teams without a budget record should not generate alerts."""
        mock_budgets_table = MagicMock()
        mock_usage_table = MagicMock()

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router

        mock_budgets_table.get_item.return_value = {}  # No Item

        result = check_and_publish_alerts([_metric(cost_usd=10.0)])

        assert result == 0
        mock_sns.publish.assert_not_called()

    @patch("cost_attribution.handler.sns")
    @patch("cost_attribution.handler.dynamodb")
    @patch("cost_attribution.handler.SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:budget-alerts")
    def test_updates_alerts_sent_in_dynamodb(self, mock_ddb: Any, mock_sns: Any) -> None:
        """After publishing alerts, alerts_sent should be updated in DynamoDB."""
        mock_budgets_table = MagicMock()
        mock_usage_table = MagicMock()

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router

        mock_budgets_table.get_item.return_value = {
            "Item": {
                "monthly_budget_usd": Decimal(1000),
                "alert_thresholds": [50, 80, 100],
                "alerts_sent": [],
            }
        }
        mock_usage_table.get_item.return_value = {"Item": {"total_cost_usd": Decimal(550)}}

        check_and_publish_alerts([_metric(cost_usd=10.0)])

        # Verify update_item was called to persist alerts_sent
        mock_budgets_table.update_item.assert_called_once()
        update_call = mock_budgets_table.update_item.call_args
        assert update_call[1]["ExpressionAttributeValues"][":as"] == [50]

    @patch("cost_attribution.handler.dynamodb")
    @patch("cost_attribution.handler.SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:budget-alerts")
    def test_unknown_team_skipped(self, mock_ddb: Any) -> None:
        """Metrics with 'unknown' team should be skipped."""
        result = check_and_publish_alerts([_metric(cost_usd=10.0, team="unknown")])
        assert result == 0


# ── handler (end-to-end) ─────────────────────────────────────────────────────


def _make_event(log_events: list[dict]) -> dict:
    """Build a CloudWatch Logs event payload."""
    payload = json.dumps({"logGroup": "test", "logEvents": log_events})
    compressed = gzip.compress(payload.encode())
    encoded = base64.b64encode(compressed).decode()
    return {"awslogs": {"data": encoded}}


class TestHandler:
    @patch("cost_attribution.handler.check_and_publish_alerts")
    @patch("cost_attribution.handler.dynamodb")
    @patch("cost_attribution.handler.cloudwatch")
    def test_valid_event(self, mock_cw: Any, mock_ddb: Any, mock_alerts: Any) -> None:
        mock_alerts.return_value = 0
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

    @patch("cost_attribution.handler.check_and_publish_alerts")
    @patch("cost_attribution.handler.dynamodb")
    @patch("cost_attribution.handler.cloudwatch")
    def test_mixed_events(self, mock_cw: Any, mock_ddb: Any, mock_alerts: Any) -> None:
        mock_alerts.return_value = 0
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

    @patch("cost_attribution.handler.check_and_publish_alerts")
    @patch("cost_attribution.handler.dynamodb")
    @patch("cost_attribution.handler.cloudwatch")
    def test_dynamodb_failure_does_not_block(self, mock_cw: Any, mock_ddb: Any, mock_alerts: Any) -> None:
        """DynamoDB write failures must not affect the CloudWatch publishing flow."""
        mock_alerts.return_value = 0
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

    @patch("cost_attribution.handler.check_and_publish_alerts")
    @patch("cost_attribution.handler.dynamodb")
    @patch("cost_attribution.handler.cloudwatch")
    def test_alert_failure_does_not_block(self, mock_cw: Any, mock_ddb: Any, mock_alerts: Any) -> None:
        """Alert publishing failures must not affect the main handler flow."""
        mock_alerts.side_effect = Exception("SNS timeout")

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
    @patch("cost_attribution.handler.check_and_publish_alerts")
    @patch("cost_attribution.handler.dynamodb")
    @patch("cost_attribution.handler.cloudwatch")
    def test_never_crashes_on_random_messages(
        self, mock_cw: Any, mock_ddb: Any, mock_alerts: Any, messages: list[str]
    ) -> None:
        mock_alerts.return_value = 0
        log_events = [{"id": str(i), "message": m} for i, m in enumerate(messages)]
        result = handler(_make_event(log_events))
        assert result["statusCode"] in (200, 400, 500)


# ── Audit log publishing ─────────────────────────────────────────────────────


class TestAuditLogPublishing:
    """Tests for _publish_audit_records (Kinesis Firehose audit trail)."""

    @patch("cost_attribution.handler._get_firehose")
    @patch("cost_attribution.handler.AUDIT_FIREHOSE_STREAM", "my-audit-stream")
    def test_sends_records_to_firehose_when_stream_set(self, mock_get_fh: Any) -> None:
        """_publish_audit_records sends records to Firehose when AUDIT_FIREHOSE_STREAM is set."""
        from cost_attribution.handler import _publish_audit_records

        mock_firehose = MagicMock()
        mock_get_fh.return_value = mock_firehose

        metrics = [_metric(provider="openai", model="gpt-4", cost_usd=0.5, team="team-a", user="u1")]
        _publish_audit_records(metrics)

        mock_firehose.put_record_batch.assert_called_once()
        call_kwargs = mock_firehose.put_record_batch.call_args[1]
        assert call_kwargs["DeliveryStreamName"] == "my-audit-stream"
        assert len(call_kwargs["Records"]) == 1
        # Verify record payload
        record_data = json.loads(call_kwargs["Records"][0]["Data"])
        assert record_data["team"] == "team-a"
        assert record_data["user_id"] == "u1"
        assert record_data["model"] == "gpt-4"
        assert record_data["provider"] == "openai"
        assert record_data["cost_usd"] == 0.5

    @patch("cost_attribution.handler._get_firehose")
    @patch("cost_attribution.handler.AUDIT_FIREHOSE_STREAM", "")
    def test_noop_when_stream_empty(self, mock_get_fh: Any) -> None:
        """_publish_audit_records is a no-op when AUDIT_FIREHOSE_STREAM is empty."""
        from cost_attribution.handler import _publish_audit_records

        _publish_audit_records([_metric()])
        mock_get_fh.assert_not_called()

    @patch("cost_attribution.handler._get_firehose")
    @patch("cost_attribution.handler.AUDIT_FIREHOSE_STREAM", "my-audit-stream")
    def test_noop_when_metrics_empty(self, mock_get_fh: Any) -> None:
        """_publish_audit_records is a no-op when metrics list is empty."""
        from cost_attribution.handler import _publish_audit_records

        _publish_audit_records([])
        mock_get_fh.assert_not_called()

    @patch("cost_attribution.handler._get_firehose")
    @patch("cost_attribution.handler.AUDIT_FIREHOSE_STREAM", "my-audit-stream")
    def test_batches_at_500_per_call(self, mock_get_fh: Any) -> None:
        """Records are batched at 500 per put_record_batch call."""
        from cost_attribution.handler import _publish_audit_records

        mock_firehose = MagicMock()
        mock_get_fh.return_value = mock_firehose

        # 1200 metrics -> 3 batches: 500, 500, 200
        metrics = [_metric(team=f"team-{i}") for i in range(1200)]
        _publish_audit_records(metrics)

        assert mock_firehose.put_record_batch.call_count == 3
        batch_sizes = [len(call[1]["Records"]) for call in mock_firehose.put_record_batch.call_args_list]
        assert batch_sizes == [500, 500, 200]

    @patch("cost_attribution.handler._publish_audit_records")
    @patch("cost_attribution.handler.check_and_publish_alerts")
    @patch("cost_attribution.handler.dynamodb")
    @patch("cost_attribution.handler.cloudwatch")
    def test_firehose_error_is_best_effort(
        self, mock_cw: Any, mock_ddb: Any, mock_alerts: Any, mock_audit: Any
    ) -> None:
        """Firehose errors are caught in handler() -- the warning is logged but handler still returns 200."""
        mock_alerts.return_value = 0
        mock_audit.side_effect = Exception("Firehose delivery failure")

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


# ── Per-team cache metrics ────────────────────────────────────────────────────


class TestPerTeamCacheMetrics:
    """Tests for per-team cache hit/miss/savings CloudWatch metrics in _publish_metrics."""

    @patch("cost_attribution.handler.cloudwatch")
    def test_cache_hit_publishes_hits_by_team(self, mock_cw: Any) -> None:
        """When cache_hit=True, CacheHitsByTeam metric is published with Team dimension."""
        from cost_attribution.handler import _publish_metrics

        m = MetricResult(
            provider="anthropic",
            model="claude-sonnet-4",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cost_usd=0.01,
            cache_hit=True,
            team="platform",
            user="u1",
        )
        _publish_metrics([m])

        # Extract all metric data points from the put_metric_data call(s)
        all_metric_data: list[dict[str, Any]] = []
        for call in mock_cw.put_metric_data.call_args_list:
            all_metric_data.extend(call[1]["MetricData"])

        # Find CacheHitsByTeam
        hits_by_team = [md for md in all_metric_data if md["MetricName"] == "CacheHitsByTeam"]
        assert len(hits_by_team) == 1
        assert hits_by_team[0]["Dimensions"] == [{"Name": "Team", "Value": "platform"}]
        assert hits_by_team[0]["Value"] == 1.0

        # CacheMissesByTeam should NOT be present
        misses_by_team = [md for md in all_metric_data if md["MetricName"] == "CacheMissesByTeam"]
        assert len(misses_by_team) == 0

    @patch("cost_attribution.handler.cloudwatch")
    def test_cache_miss_publishes_misses_by_team(self, mock_cw: Any) -> None:
        """When cache_hit=False, CacheMissesByTeam metric is published with Team dimension."""
        from cost_attribution.handler import _publish_metrics

        m = MetricResult(
            provider="openai",
            model="gpt-4",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            cost_usd=0.01,
            cache_hit=False,
            team="infra",
            user="u2",
        )
        _publish_metrics([m])

        all_metric_data: list[dict[str, Any]] = []
        for call in mock_cw.put_metric_data.call_args_list:
            all_metric_data.extend(call[1]["MetricData"])

        # Find CacheMissesByTeam
        misses_by_team = [md for md in all_metric_data if md["MetricName"] == "CacheMissesByTeam"]
        assert len(misses_by_team) == 1
        assert misses_by_team[0]["Dimensions"] == [{"Name": "Team", "Value": "infra"}]
        assert misses_by_team[0]["Value"] == 1.0

        # CacheHitsByTeam should NOT be present
        hits_by_team = [md for md in all_metric_data if md["MetricName"] == "CacheHitsByTeam"]
        assert len(hits_by_team) == 0

    @patch("cost_attribution.handler.cloudwatch")
    def test_cache_savings_by_team_always_published(self, mock_cw: Any) -> None:
        """CacheSavingsByTeam metric is always published with cache_savings_usd value."""
        from cost_attribution.handler import _publish_metrics

        m = MetricResult(
            provider="anthropic",
            model="claude-sonnet-4",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            cost_usd=0.01,
            cache_savings_usd=0.005,
            cache_hit=True,
            team="data-eng",
            user="u3",
        )
        _publish_metrics([m])

        all_metric_data: list[dict[str, Any]] = []
        for call in mock_cw.put_metric_data.call_args_list:
            all_metric_data.extend(call[1]["MetricData"])

        savings_by_team = [md for md in all_metric_data if md["MetricName"] == "CacheSavingsByTeam"]
        assert len(savings_by_team) == 1
        assert savings_by_team[0]["Dimensions"] == [{"Name": "Team", "Value": "data-eng"}]
        assert savings_by_team[0]["Value"] == 0.005
        assert savings_by_team[0]["Unit"] == "None"
