"""Tests for the rate limiter module.

Covers RPM limit enforcement, daily token limit enforcement,
skip conditions (zero RPM limit, unlimited tokens, zero estimated tokens),
and graceful degradation when DynamoDB is unreachable.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from botocore.exceptions import ClientError

from rate_limiter.handler import check_rate_limit
from rate_limiter.models import RateLimitResult

# ── Helpers ──────────────────────────────────────────────────────────────────


def _ddb_client_error(code: str = "ServiceUnavailable", message: str = "DDB down") -> ClientError:
    """Build a botocore ClientError for DynamoDB failure simulation."""
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "UpdateItem",
    )


# ── RateLimitResult model ───────────────────────────────────────────────────


class TestRateLimitResult:
    def test_defaults(self) -> None:
        result = RateLimitResult(allowed=True)
        assert result.allowed is True
        assert result.reason == ""
        assert result.retry_after_seconds is None
        assert result.current_rpm == 0
        assert result.current_daily_tokens == 0

    def test_denied_with_retry(self) -> None:
        result = RateLimitResult(
            allowed=False,
            reason="RPM limit exceeded",
            retry_after_seconds=30,
            current_rpm=100,
            current_daily_tokens=5000,
        )
        assert result.allowed is False
        assert result.retry_after_seconds == 30
        assert result.current_rpm == 100
        assert result.current_daily_tokens == 5000

    def test_serialization(self) -> None:
        result = RateLimitResult(allowed=True, current_rpm=5)
        dumped = result.model_dump(exclude_none=True)
        assert dumped["allowed"] is True
        assert dumped["current_rpm"] == 5
        assert "retry_after_seconds" not in dumped


# ── RPM checks ──────────────────────────────────────────────────────────────


class TestRPMLimit:
    @patch("rate_limiter.handler._increment_rpm_counter")
    def test_request_allowed_under_rpm_limit(self, mock_rpm: Any) -> None:
        """Request is allowed when current RPM is below the limit."""
        mock_rpm.return_value = 5  # 5 requests this minute

        result = check_rate_limit(team="platform", rpm_limit=100, tokens_per_day_limit=-1, estimated_tokens=0)

        assert result.allowed is True
        assert result.current_rpm == 5
        mock_rpm.assert_called_once_with("platform")

    @patch("rate_limiter.handler._increment_rpm_counter")
    def test_request_denied_rpm_limit_exceeded(self, mock_rpm: Any) -> None:
        """Request is denied when current RPM exceeds the limit."""
        mock_rpm.return_value = 101  # Over the 100 RPM limit

        result = check_rate_limit(team="platform", rpm_limit=100, tokens_per_day_limit=-1, estimated_tokens=0)

        assert result.allowed is False
        assert "RPM limit exceeded" in result.reason
        assert "101/100" in result.reason
        assert result.retry_after_seconds is not None
        assert result.retry_after_seconds >= 1
        assert result.retry_after_seconds <= 60
        assert result.current_rpm == 101

    @patch("rate_limiter.handler._increment_rpm_counter")
    def test_request_allowed_at_exact_rpm_limit(self, mock_rpm: Any) -> None:
        """Request is allowed when current RPM is exactly at the limit (not over)."""
        mock_rpm.return_value = 100  # Exactly at the 100 RPM limit

        result = check_rate_limit(team="platform", rpm_limit=100, tokens_per_day_limit=-1, estimated_tokens=0)

        assert result.allowed is True
        assert result.current_rpm == 100

    @patch("rate_limiter.handler._increment_rpm_counter")
    def test_rpm_zero_skips_check(self, mock_rpm: Any) -> None:
        """RPM limit of 0 means the RPM check is skipped entirely."""
        result = check_rate_limit(team="platform", rpm_limit=0, tokens_per_day_limit=-1, estimated_tokens=0)

        assert result.allowed is True
        assert result.current_rpm == 0
        mock_rpm.assert_not_called()


# ── Daily token checks ──────────────────────────────────────────────────────


class TestDailyTokenLimit:
    @patch("rate_limiter.handler._increment_daily_token_counter")
    @patch("rate_limiter.handler._increment_rpm_counter")
    def test_request_allowed_under_daily_token_limit(self, mock_rpm: Any, mock_tokens: Any) -> None:
        """Request is allowed when daily token usage is below the limit."""
        mock_rpm.return_value = 1
        mock_tokens.return_value = 50000

        result = check_rate_limit(team="platform", rpm_limit=100, tokens_per_day_limit=1000000, estimated_tokens=500)

        assert result.allowed is True
        assert result.current_daily_tokens == 50000
        mock_tokens.assert_called_once_with("platform", 500)

    @patch("rate_limiter.handler._increment_daily_token_counter")
    @patch("rate_limiter.handler._increment_rpm_counter")
    def test_request_denied_daily_token_limit_exceeded(self, mock_rpm: Any, mock_tokens: Any) -> None:
        """Request is denied when daily token usage exceeds the limit."""
        mock_rpm.return_value = 1
        mock_tokens.return_value = 1100000  # Over the 1M limit

        result = check_rate_limit(team="platform", rpm_limit=100, tokens_per_day_limit=1000000, estimated_tokens=500)

        assert result.allowed is False
        assert "Daily token limit exceeded" in result.reason
        assert result.retry_after_seconds is not None
        assert result.retry_after_seconds >= 1
        assert result.current_daily_tokens == 1100000

    @patch("rate_limiter.handler._increment_daily_token_counter")
    @patch("rate_limiter.handler._increment_rpm_counter")
    def test_request_allowed_at_exact_daily_token_limit(self, mock_rpm: Any, mock_tokens: Any) -> None:
        """Request is allowed when daily token usage is exactly at the limit."""
        mock_rpm.return_value = 1
        mock_tokens.return_value = 1000000  # Exactly at 1M

        result = check_rate_limit(team="platform", rpm_limit=100, tokens_per_day_limit=1000000, estimated_tokens=500)

        assert result.allowed is True
        assert result.current_daily_tokens == 1000000

    @patch("rate_limiter.handler._increment_daily_token_counter")
    @patch("rate_limiter.handler._increment_rpm_counter")
    def test_tokens_per_day_unlimited_skips_check(self, mock_rpm: Any, mock_tokens: Any) -> None:
        """tokens_per_day_limit of -1 means the daily token check is skipped."""
        mock_rpm.return_value = 1

        result = check_rate_limit(team="platform", rpm_limit=100, tokens_per_day_limit=-1, estimated_tokens=500)

        assert result.allowed is True
        mock_tokens.assert_not_called()

    @patch("rate_limiter.handler._increment_daily_token_counter")
    @patch("rate_limiter.handler._increment_rpm_counter")
    def test_estimated_tokens_zero_skips_check(self, mock_rpm: Any, mock_tokens: Any) -> None:
        """estimated_tokens of 0 means the daily token check is skipped."""
        mock_rpm.return_value = 1

        result = check_rate_limit(team="platform", rpm_limit=100, tokens_per_day_limit=1000000, estimated_tokens=0)

        assert result.allowed is True
        mock_tokens.assert_not_called()


# ── Graceful degradation ────────────────────────────────────────────────────


class TestGracefulDegradation:
    @patch("rate_limiter.handler._increment_rpm_counter")
    def test_ddb_error_during_rpm_check_allows_request(self, mock_rpm: Any) -> None:
        """DynamoDB error during RPM check returns allowed=True with degraded reason."""
        mock_rpm.side_effect = _ddb_client_error()

        result = check_rate_limit(team="platform", rpm_limit=100, tokens_per_day_limit=1000000, estimated_tokens=500)

        assert result.allowed is True
        assert result.reason == "rate-limit-degraded"

    @patch("rate_limiter.handler._increment_rpm_counter")
    def test_ddb_generic_exception_during_rpm_check_allows_request(self, mock_rpm: Any) -> None:
        """Any exception during RPM check returns allowed=True with degraded reason."""
        mock_rpm.side_effect = RuntimeError("connection timeout")

        result = check_rate_limit(team="platform", rpm_limit=100, tokens_per_day_limit=1000000, estimated_tokens=500)

        assert result.allowed is True
        assert result.reason == "rate-limit-degraded"

    @patch("rate_limiter.handler._increment_daily_token_counter")
    @patch("rate_limiter.handler._increment_rpm_counter")
    def test_ddb_error_during_token_check_allows_request(self, mock_rpm: Any, mock_tokens: Any) -> None:
        """DynamoDB error during daily token check returns allowed=True with degraded reason."""
        mock_rpm.return_value = 5  # RPM check passes
        mock_tokens.side_effect = _ddb_client_error()

        result = check_rate_limit(team="platform", rpm_limit=100, tokens_per_day_limit=1000000, estimated_tokens=500)

        assert result.allowed is True
        assert result.reason == "rate-limit-degraded"
        assert result.current_rpm == 5

    @patch("rate_limiter.handler._increment_daily_token_counter")
    @patch("rate_limiter.handler._increment_rpm_counter")
    def test_ddb_generic_exception_during_token_check_allows_request(self, mock_rpm: Any, mock_tokens: Any) -> None:
        """Any exception during daily token check returns allowed=True."""
        mock_rpm.return_value = 5
        mock_tokens.side_effect = RuntimeError("network error")

        result = check_rate_limit(team="platform", rpm_limit=100, tokens_per_day_limit=1000000, estimated_tokens=500)

        assert result.allowed is True
        assert result.reason == "rate-limit-degraded"


# ── Combined scenarios ──────────────────────────────────────────────────────


class TestCombinedScenarios:
    @patch("rate_limiter.handler._increment_daily_token_counter")
    @patch("rate_limiter.handler._increment_rpm_counter")
    def test_both_limits_within_bounds(self, mock_rpm: Any, mock_tokens: Any) -> None:
        """Both RPM and daily token usage within limits returns allowed."""
        mock_rpm.return_value = 10
        mock_tokens.return_value = 50000

        result = check_rate_limit(team="platform", rpm_limit=100, tokens_per_day_limit=1000000, estimated_tokens=500)

        assert result.allowed is True
        assert result.current_rpm == 10
        assert result.current_daily_tokens == 50000

    @patch("rate_limiter.handler._increment_daily_token_counter")
    @patch("rate_limiter.handler._increment_rpm_counter")
    def test_rpm_exceeded_before_token_check(self, mock_rpm: Any, mock_tokens: Any) -> None:
        """RPM exceeded short-circuits before the daily token check runs."""
        mock_rpm.return_value = 200  # Over limit

        result = check_rate_limit(team="platform", rpm_limit=100, tokens_per_day_limit=1000000, estimated_tokens=500)

        assert result.allowed is False
        assert "RPM limit exceeded" in result.reason
        mock_tokens.assert_not_called()  # Token check never reached

    @patch("rate_limiter.handler._increment_rpm_counter")
    def test_all_checks_skipped_returns_allowed(self, mock_rpm: Any) -> None:
        """RPM=0, tokens_per_day=-1, estimated_tokens=0 skips all checks."""
        result = check_rate_limit(team="platform", rpm_limit=0, tokens_per_day_limit=-1, estimated_tokens=0)

        assert result.allowed is True
        assert result.current_rpm == 0
        assert result.current_daily_tokens == 0
        mock_rpm.assert_not_called()

    @patch("rate_limiter.handler._increment_daily_token_counter")
    @patch("rate_limiter.handler._increment_rpm_counter")
    def test_rpm_ok_tokens_exceeded(self, mock_rpm: Any, mock_tokens: Any) -> None:
        """RPM within limits but daily tokens exceeded denies the request."""
        mock_rpm.return_value = 5
        mock_tokens.return_value = 2000000  # Over 1M limit

        result = check_rate_limit(team="data-eng", rpm_limit=100, tokens_per_day_limit=1000000, estimated_tokens=1000)

        assert result.allowed is False
        assert "Daily token limit exceeded" in result.reason
        assert result.current_rpm == 5
        assert result.current_daily_tokens == 2000000
