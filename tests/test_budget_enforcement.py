"""Tests for the budget enforcement Lambda.

Covers JWT extraction, budget checking (within budget, at threshold, exceeded),
tier default fallback (E.4), model-level budget caps (E.5),
and DynamoDB failure graceful degradation.
Uses hypothesis for property-based tests.
"""

from __future__ import annotations

import base64
import json
import os
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from budget_enforcement.handler import (
    TIER_DEFAULTS,
    _build_response,
    _check_budget,
    _check_model_budget,
    _load_tier_defaults,
    _parse_model_limits,
    _seconds_until_period_reset,
    handler,
)
from budget_enforcement.jwt_utils import (
    decode_jwt_payload,
    extract_cost_center,
    extract_team,
    extract_tenant_tier,
    extract_user,
)
from budget_enforcement.models import (
    BudgetCheckRequest,
    BudgetCheckResponse,
    BudgetStatus,
    ModelBudgetError,
    ModelLimit,
    PluginHandlerResponse,
    TierConfig,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_jwt(claims: dict[str, Any]) -> str:
    """Build a fake JWT with the given payload claims."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(b"fakesig").decode().rstrip("=")
    return f"{header}.{payload}.{signature}"


def _make_function_url_event(body: dict[str, Any]) -> dict[str, Any]:
    """Build a Lambda Function URL event."""
    return {
        "body": json.dumps(body),
        "isBase64Encoded": False,
        "requestContext": {"http": {"method": "POST"}},
    }


# ── JWT utilities ────────────────────────────────────────────────────────────


class TestDecodeJwtPayload:
    def test_valid_jwt(self) -> None:
        claims = {"sub": "user-123", "custom:team": "platform"}
        token = _make_jwt(claims)
        decoded = decode_jwt_payload(token)
        assert decoded["sub"] == "user-123"
        assert decoded["custom:team"] == "platform"

    def test_empty_string(self) -> None:
        assert decode_jwt_payload("") == {}

    def test_single_part(self) -> None:
        assert decode_jwt_payload("noperiods") == {}

    def test_invalid_base64(self) -> None:
        assert decode_jwt_payload("a.!!!invalid!!!.c") == {}

    def test_non_dict_payload(self) -> None:
        payload = base64.urlsafe_b64encode(json.dumps([1, 2, 3]).encode()).decode()
        assert decode_jwt_payload(f"h.{payload}.s") == {}

    @given(token=st.text(max_size=500))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_never_crashes_on_random_input(self, token: str) -> None:
        result = decode_jwt_payload(token)
        assert isinstance(result, dict)


class TestExtractTeam:
    def test_custom_team(self) -> None:
        assert extract_team({"custom:team": "data-eng"}) == "data-eng"

    def test_fallback_team(self) -> None:
        assert extract_team({"team": "ml-ops"}) == "ml-ops"

    def test_missing(self) -> None:
        assert extract_team({}) == "unknown"

    def test_empty_string(self) -> None:
        assert extract_team({"custom:team": ""}) == "unknown"

    @given(claims=st.dictionaries(st.text(max_size=30), st.text(max_size=30), max_size=5))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_never_crashes(self, claims: dict[str, str]) -> None:
        result = extract_team(claims)
        assert isinstance(result, str)
        assert len(result) > 0


class TestExtractUser:
    def test_sub(self) -> None:
        assert extract_user({"sub": "abc-123"}) == "abc-123"

    def test_username_fallback(self) -> None:
        assert extract_user({"username": "jdoe"}) == "jdoe"

    def test_missing(self) -> None:
        assert extract_user({}) == "unknown"


class TestExtractCostCenter:
    def test_custom_cost_center(self) -> None:
        assert extract_cost_center({"custom:cost_center": "CC-1234"}) == "CC-1234"

    def test_missing(self) -> None:
        assert extract_cost_center({}) == ""


class TestExtractTenantTier:
    def test_custom_tier(self) -> None:
        assert extract_tenant_tier({"custom:tenant_tier": "Premium"}) == "premium"

    def test_default(self) -> None:
        assert extract_tenant_tier({}) == "standard"


# ── E.4: Tier config model & loading ────────────────────────────────────────


class TestTierConfig:
    def test_tier_config_creation(self) -> None:
        tc = TierConfig(rpm=100, tokens_per_day=500000, monthly_usd=100)
        assert tc.rpm == 100
        assert tc.tokens_per_day == 500000
        assert tc.monthly_usd == Decimal(100)

    def test_tier_config_unlimited_tokens(self) -> None:
        tc = TierConfig(rpm=2000, tokens_per_day=-1, monthly_usd=10000)
        assert tc.tokens_per_day == -1

    def test_default_tier_defaults_loaded(self) -> None:
        """Built-in defaults should include sandbox, standard, premium, unlimited."""
        assert "sandbox" in TIER_DEFAULTS or "standard" in TIER_DEFAULTS
        for tier_cfg in TIER_DEFAULTS.values():
            assert isinstance(tier_cfg, TierConfig)
            assert tier_cfg.rpm >= 0
            assert tier_cfg.monthly_usd >= 0

    def test_load_tier_defaults_from_env(self) -> None:
        """TIER_DEFAULTS env var should override built-in defaults."""
        tier_json = json.dumps(
            {
                "bronze": {"rpm": 10, "tokens_per_day": 50000, "monthly_usd": 10},
                "gold": {"rpm": 1000, "tokens_per_day": 10000000, "monthly_usd": 5000},
            }
        )
        with patch.dict(os.environ, {"TIER_DEFAULTS": tier_json}, clear=False):
            result = _load_tier_defaults()
        assert "bronze" in result
        assert "gold" in result
        assert result["bronze"].rpm == 10
        assert result["gold"].monthly_usd == Decimal(5000)

    def test_load_tier_defaults_invalid_json_falls_back(self) -> None:
        """Invalid JSON in TIER_DEFAULTS should fall back to legacy or built-in."""
        with patch.dict(os.environ, {"TIER_DEFAULTS": "not json!!!"}, clear=False):
            result = _load_tier_defaults()
        assert len(result) > 0

    def test_load_tier_defaults_legacy_env_vars(self) -> None:
        """Legacy per-tier env vars should be used when TIER_DEFAULTS is absent."""
        env = {
            "TIER_DEFAULT_FREE": "5",
            "TIER_DEFAULT_STANDARD": "500",
            "TIER_DEFAULT_PREMIUM": "5000",
            "TIER_DEFAULT_ENTERPRISE": "50000",
        }
        with patch.dict(os.environ, env, clear=False):
            # Remove TIER_DEFAULTS if present
            os.environ.pop("TIER_DEFAULTS", None)
            result = _load_tier_defaults()
        assert result["free"].monthly_usd == Decimal(5)
        assert result["standard"].monthly_usd == Decimal(500)

    def test_tier_default_fallback_for_unknown_tier(self) -> None:
        """Unknown tier should fall back to standard."""
        with (
            patch("budget_enforcement.handler._get_budget_record") as mock_budget,
            patch("budget_enforcement.handler._get_current_usage") as mock_usage,
        ):
            mock_budget.return_value = None
            mock_usage.return_value = Decimal("5.00")

            jwt = _make_jwt({"custom:team": "new-team", "sub": "user1", "custom:tenant_tier": "nonexistent"})
            req = BudgetCheckRequest(jwt_token=jwt)
            result = _check_budget(req)

            assert result.allowed is True
            assert result.budget_status is not None
            # Should have fallen back to standard tier budget


# ── Budget check logic ───────────────────────────────────────────────────────


class TestCheckBudget:
    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_within_budget(self, mock_budget: Any, mock_usage: Any) -> None:
        """Request allowed when usage is well below budget."""
        mock_budget.return_value = {
            "monthly_budget_usd": "1000",
            "warn_threshold_pct": 80,
            "hard_limit_pct": 100,
        }
        mock_usage.return_value = Decimal("100.00")

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        req = BudgetCheckRequest(jwt_token=jwt)
        result = _check_budget(req)

        assert result.allowed is True
        assert result.budget_status is not None
        assert result.budget_status.team == "platform"
        assert result.budget_status.utilization_pct == pytest.approx(10.0)

    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_at_warning_threshold(self, mock_budget: Any, mock_usage: Any) -> None:
        """Request allowed with warning when at 80% utilization."""
        mock_budget.return_value = {
            "monthly_budget_usd": "1000",
            "warn_threshold_pct": 80,
            "hard_limit_pct": 100,
        }
        mock_usage.return_value = Decimal("850.00")

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        req = BudgetCheckRequest(jwt_token=jwt)
        result = _check_budget(req)

        assert result.allowed is True
        assert "warning" in result.reason.lower()

    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_budget_exceeded(self, mock_budget: Any, mock_usage: Any) -> None:
        """Request blocked when usage exceeds hard limit."""
        mock_budget.return_value = {
            "monthly_budget_usd": "1000",
            "warn_threshold_pct": 80,
            "hard_limit_pct": 100,
        }
        mock_usage.return_value = Decimal("1050.00")

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        req = BudgetCheckRequest(jwt_token=jwt)
        result = _check_budget(req)

        assert result.allowed is False
        assert result.status_code == 429
        assert result.retry_after_seconds is not None
        assert result.retry_after_seconds > 0
        assert "exceeded" in result.reason.lower()

    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_exactly_at_hard_limit(self, mock_budget: Any, mock_usage: Any) -> None:
        """Request blocked when usage is exactly at 100% of budget."""
        mock_budget.return_value = {
            "monthly_budget_usd": "500",
            "warn_threshold_pct": 80,
            "hard_limit_pct": 100,
        }
        mock_usage.return_value = Decimal("500.00")

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        req = BudgetCheckRequest(jwt_token=jwt)
        result = _check_budget(req)

        assert result.allowed is False
        assert result.status_code == 429

    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_tier_default_fallback(self, mock_budget: Any, mock_usage: Any) -> None:
        """When no budget record exists, fall back to tier defaults."""
        mock_budget.return_value = None  # No budget record
        mock_usage.return_value = Decimal("5.00")

        jwt = _make_jwt({"custom:team": "new-team", "sub": "user1", "custom:tenant_tier": "sandbox"})
        req = BudgetCheckRequest(jwt_token=jwt)
        result = _check_budget(req)

        assert result.allowed is True
        assert result.budget_status is not None
        # Sandbox tier default budget
        assert result.budget_status.monthly_budget_usd == TIER_DEFAULTS["sandbox"].monthly_usd

    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_tier_default_exceeded(self, mock_budget: Any, mock_usage: Any) -> None:
        """Sandbox tier budget exceeded uses tier defaults."""
        mock_budget.return_value = None
        mock_usage.return_value = Decimal("30.00")  # Over the $25 sandbox tier

        jwt = _make_jwt({"custom:team": "free-team", "sub": "user1", "custom:tenant_tier": "sandbox"})
        req = BudgetCheckRequest(jwt_token=jwt)
        result = _check_budget(req)

        assert result.allowed is False
        assert result.status_code == 429

    @patch("budget_enforcement.handler._get_budget_record")
    def test_dynamodb_budget_failure_allows_request(self, mock_budget: Any) -> None:
        """If DynamoDB is unreachable for budget lookup, allow the request."""
        mock_budget.side_effect = ClientError(
            {"Error": {"Code": "ServiceUnavailable", "Message": "DDB down"}},
            "GetItem",
        )

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        req = BudgetCheckRequest(jwt_token=jwt)
        result = _check_budget(req)

        assert result.allowed is True
        assert result.reason == "budget-check-degraded"

    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_dynamodb_usage_failure_allows_request(self, mock_budget: Any, mock_usage: Any) -> None:
        """If DynamoDB is unreachable for usage lookup, allow the request."""
        mock_budget.return_value = {"monthly_budget_usd": "1000", "warn_threshold_pct": 80, "hard_limit_pct": 100}
        mock_usage.side_effect = ClientError(
            {"Error": {"Code": "ServiceUnavailable", "Message": "DDB down"}},
            "GetItem",
        )

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        req = BudgetCheckRequest(jwt_token=jwt)
        result = _check_budget(req)

        assert result.allowed is True
        assert result.reason == "budget-check-degraded"

    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_zero_budget_does_not_divide_by_zero(self, mock_budget: Any, mock_usage: Any) -> None:
        """Zero budget should not cause a division error."""
        mock_budget.return_value = {
            "monthly_budget_usd": "0",
            "warn_threshold_pct": 80,
            "hard_limit_pct": 100,
        }
        mock_usage.return_value = Decimal(0)

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        req = BudgetCheckRequest(jwt_token=jwt)
        result = _check_budget(req)

        assert result.budget_status is not None
        assert result.budget_status.utilization_pct == 0.0


# ── E.5: Model-level budget caps ─────────────────────────────────────────────


class TestModelLevelBudgets:
    def test_model_limit_model(self) -> None:
        ml = ModelLimit(monthly_usd=Decimal(200), daily_tokens=100000)
        assert ml.monthly_usd == Decimal(200)
        assert ml.daily_tokens == 100000

    def test_model_limit_defaults(self) -> None:
        ml = ModelLimit(monthly_usd=Decimal(0))
        assert ml.daily_tokens == -1  # Unlimited by default

    def test_parse_model_limits_valid(self) -> None:
        budget_item = {
            "model_limits": {
                "claude-opus-4": {"monthly_usd": "200", "daily_tokens": 100000},
                "gpt-4.1": {"monthly_usd": "150"},
            }
        }
        result = _parse_model_limits(budget_item)
        assert "claude-opus-4" in result
        assert "gpt-4.1" in result
        assert result["claude-opus-4"].monthly_usd == Decimal(200)
        assert result["gpt-4.1"].daily_tokens == -1

    def test_parse_model_limits_empty(self) -> None:
        assert _parse_model_limits({}) == {}
        assert _parse_model_limits({"model_limits": None}) == {}
        assert _parse_model_limits({"model_limits": "not a dict"}) == {}

    def test_parse_model_limits_invalid_entry_skipped(self) -> None:
        budget_item = {
            "model_limits": {
                "good-model": {"monthly_usd": "100"},
                "bad-model": "not a dict",
            }
        }
        result = _parse_model_limits(budget_item)
        assert "good-model" in result
        assert "bad-model" not in result

    @patch("budget_enforcement.handler._get_model_usage")
    def test_check_model_budget_within_limit(self, mock_model_usage: Any) -> None:
        mock_model_usage.return_value = Decimal("150.00")
        limits = {"claude-opus-4": ModelLimit(monthly_usd=Decimal(200), daily_tokens=100000)}
        result = _check_model_budget("team-a", "claude-opus-4", limits)
        assert result is None

    @patch("budget_enforcement.handler._get_model_usage")
    def test_check_model_budget_exceeded(self, mock_model_usage: Any) -> None:
        mock_model_usage.return_value = Decimal("215.50")
        limits = {"claude-opus-4": ModelLimit(monthly_usd=Decimal(200), daily_tokens=100000)}
        result = _check_model_budget("team-a", "claude-opus-4", limits)
        assert result is not None
        assert result.type == "model_budget_exceeded"
        assert result.model == "claude-opus-4"
        assert result.limit_usd == Decimal(200)
        assert result.current_usd == Decimal("215.50")

    @patch("budget_enforcement.handler._get_model_usage")
    def test_check_model_budget_exactly_at_limit(self, mock_model_usage: Any) -> None:
        mock_model_usage.return_value = Decimal("200.00")
        limits = {"claude-opus-4": ModelLimit(monthly_usd=Decimal(200), daily_tokens=100000)}
        result = _check_model_budget("team-a", "claude-opus-4", limits)
        assert result is not None  # At limit = exceeded

    def test_check_model_budget_unknown_model(self) -> None:
        limits = {"claude-opus-4": ModelLimit(monthly_usd=Decimal(200), daily_tokens=100000)}
        result = _check_model_budget("team-a", "unknown", limits)
        assert result is None

    def test_check_model_budget_model_not_in_limits(self) -> None:
        limits = {"claude-opus-4": ModelLimit(monthly_usd=Decimal(200), daily_tokens=100000)}
        result = _check_model_budget("team-a", "gpt-4.1", limits)
        assert result is None

    def test_check_model_budget_empty_limits(self) -> None:
        result = _check_model_budget("team-a", "claude-opus-4", {})
        assert result is None

    @patch("budget_enforcement.handler._get_model_usage")
    def test_check_model_budget_dynamodb_failure_graceful(self, mock_model_usage: Any) -> None:
        """DynamoDB failure for model usage lookup should gracefully allow."""
        mock_model_usage.side_effect = ClientError(
            {"Error": {"Code": "ServiceUnavailable", "Message": "DDB down"}},
            "GetItem",
        )
        limits = {"claude-opus-4": ModelLimit(monthly_usd=Decimal(200), daily_tokens=100000)}
        result = _check_model_budget("team-a", "claude-opus-4", limits)
        assert result is None  # Graceful degradation

    @patch("budget_enforcement.handler._get_model_usage")
    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_model_budget_exceeded_in_handler(self, mock_budget: Any, mock_usage: Any, mock_model_usage: Any) -> None:
        """End-to-end: model budget exceeded returns proper error shape."""
        mock_budget.return_value = {
            "monthly_budget_usd": "10000",
            "warn_threshold_pct": 80,
            "hard_limit_pct": 100,
            "model_limits": {
                "claude-opus-4": {"monthly_usd": "200", "daily_tokens": 100000},
            },
        }
        mock_usage.return_value = Decimal("500.00")  # Team budget OK
        mock_model_usage.return_value = Decimal("215.50")  # Model budget exceeded

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        req = BudgetCheckRequest(jwt_token=jwt, model="claude-opus-4")
        result = _check_budget(req)

        assert result.allowed is False
        assert result.status_code == 429
        assert result.error is not None
        assert result.error.type == "model_budget_exceeded"
        assert result.error.model == "claude-opus-4"
        assert result.error.limit_usd == Decimal(200)
        assert result.error.current_usd == Decimal("215.50")

    @patch("budget_enforcement.handler._get_model_usage")
    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_model_budget_ok_team_budget_ok(self, mock_budget: Any, mock_usage: Any, mock_model_usage: Any) -> None:
        """Both team and model budgets within limits."""
        mock_budget.return_value = {
            "monthly_budget_usd": "10000",
            "warn_threshold_pct": 80,
            "hard_limit_pct": 100,
            "model_limits": {
                "claude-opus-4": {"monthly_usd": "200", "daily_tokens": 100000},
            },
        }
        mock_usage.return_value = Decimal("500.00")
        mock_model_usage.return_value = Decimal("100.00")

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        req = BudgetCheckRequest(jwt_token=jwt, model="claude-opus-4")
        result = _check_budget(req)

        assert result.allowed is True
        assert result.error is None

    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_team_budget_exceeded_before_model_check(self, mock_budget: Any, mock_usage: Any) -> None:
        """Team budget exceeded takes precedence over model budget check."""
        mock_budget.return_value = {
            "monthly_budget_usd": "1000",
            "warn_threshold_pct": 80,
            "hard_limit_pct": 100,
            "model_limits": {
                "claude-opus-4": {"monthly_usd": "200"},
            },
        }
        mock_usage.return_value = Decimal("1100.00")  # Team budget exceeded

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        req = BudgetCheckRequest(jwt_token=jwt, model="claude-opus-4")
        result = _check_budget(req)

        assert result.allowed is False
        assert "Monthly budget exceeded" in result.reason
        assert result.error is None  # Model error not set for team-level exceed


# ── Handler (Function URL integration) ──────────────────────────────────────


class TestBudgetHandler:
    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_valid_request(self, mock_budget: Any, mock_usage: Any) -> None:
        mock_budget.return_value = {"monthly_budget_usd": "1000", "warn_threshold_pct": 80, "hard_limit_pct": 100}
        mock_usage.return_value = Decimal("50.00")

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        event = _make_function_url_event({"jwt_token": jwt})
        result = handler(event)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["verdict"] is True
        assert "error" not in body

    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_blocked_request(self, mock_budget: Any, mock_usage: Any) -> None:
        mock_budget.return_value = {"monthly_budget_usd": "100", "warn_threshold_pct": 80, "hard_limit_pct": 100}
        mock_usage.return_value = Decimal("150.00")

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        event = _make_function_url_event({"jwt_token": jwt})
        result = handler(event)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["verdict"] is False
        assert "error" in body
        assert "exceeded" in body["error"].lower()

    def test_invalid_body(self) -> None:
        event = {"body": "not json!!!", "isBase64Encoded": False}
        result = handler(event)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["verdict"] is False
        assert body["error"] == "Invalid request body"

    def test_missing_jwt_token(self) -> None:
        event = _make_function_url_event({"model": "gpt-4"})
        result = handler(event)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["verdict"] is False
        assert "Validation error" in body["error"]

    def test_base64_encoded_body(self) -> None:
        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        raw_body = json.dumps({"jwt_token": jwt})
        encoded_body = base64.b64encode(raw_body.encode()).decode()
        event = {"body": encoded_body, "isBase64Encoded": True}
        with (
            patch("budget_enforcement.handler._get_budget_record") as mock_budget,
            patch("budget_enforcement.handler._get_current_usage") as mock_usage,
        ):
            mock_budget.return_value = {"monthly_budget_usd": "1000", "warn_threshold_pct": 80, "hard_limit_pct": 100}
            mock_usage.return_value = Decimal(0)
            result = handler(event)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["verdict"] is True

    @patch("budget_enforcement.handler._get_model_usage")
    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_model_budget_exceeded_response_shape(
        self, mock_budget: Any, mock_usage: Any, mock_model_usage: Any
    ) -> None:
        """E.5: Full handler returns correct JSON shape for model budget exceeded."""
        mock_budget.return_value = {
            "monthly_budget_usd": "10000",
            "warn_threshold_pct": 80,
            "hard_limit_pct": 100,
            "model_limits": {
                "claude-opus-4": {"monthly_usd": "200", "daily_tokens": 100000},
            },
        }
        mock_usage.return_value = Decimal("500.00")
        mock_model_usage.return_value = Decimal("215.50")

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        event = _make_function_url_event({"jwt_token": jwt, "model": "claude-opus-4"})
        result = handler(event)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["verdict"] is False
        assert "error" in body
        assert "Model budget exceeded" in body["error"]
        assert "budget_status" in body["data"]


# ── Seconds until period reset ───────────────────────────────────────────────


class TestSecondsUntilPeriodReset:
    def test_positive(self) -> None:
        seconds = _seconds_until_period_reset()
        assert seconds >= 1


# ── Models ───────────────────────────────────────────────────────────────────


class TestBudgetModels:
    def test_budget_check_request_defaults(self) -> None:
        req = BudgetCheckRequest(jwt_token="a.b.c")  # noqa: S106
        assert req.model == "unknown"
        assert req.provider == "unknown"
        assert req.estimated_tokens == 0

    def test_budget_check_response_serialization(self) -> None:
        resp = BudgetCheckResponse(
            allowed=False,
            status_code=429,
            reason="Budget exceeded",
            retry_after_seconds=3600,
        )
        dumped = resp.model_dump(exclude_none=True)
        assert dumped["allowed"] is False
        assert dumped["retry_after_seconds"] == 3600

    def test_budget_check_response_with_model_error(self) -> None:
        error = ModelBudgetError(
            type="model_budget_exceeded",
            model="claude-opus-4",
            limit_usd=Decimal(200),
            current_usd=Decimal("215.50"),
        )
        resp = BudgetCheckResponse(
            allowed=False,
            status_code=429,
            reason="Model budget exceeded",
            error=error,
        )
        dumped = resp.model_dump(exclude_none=True, mode="json")
        assert dumped["error"]["type"] == "model_budget_exceeded"
        assert dumped["error"]["model"] == "claude-opus-4"

    def test_budget_status_defaults(self) -> None:
        status = BudgetStatus(team="t", user="u")
        assert status.utilization_pct == 0.0
        assert status.warn_threshold_pct == 80.0
        assert status.hard_limit_pct == 100.0

    def test_plugin_handler_response_allow(self) -> None:
        resp = PluginHandlerResponse(verdict=True, data={"budget_status": {"team": "t"}})
        dumped = resp.model_dump(exclude_none=True, mode="json")
        assert dumped["verdict"] is True
        assert dumped["data"]["budget_status"]["team"] == "t"
        assert "error" not in dumped

    def test_plugin_handler_response_deny(self) -> None:
        resp = PluginHandlerResponse(verdict=False, error="Budget exceeded")
        dumped = resp.model_dump(exclude_none=True, mode="json")
        assert dumped["verdict"] is False
        assert dumped["error"] == "Budget exceeded"


# ── Build response ───────────────────────────────────────────────────────────


class TestBuildResponse:
    def test_allowed_format(self) -> None:
        resp = BudgetCheckResponse(allowed=True)
        result = _build_response(resp)
        assert result["statusCode"] == 200
        assert result["headers"]["Content-Type"] == "application/json"
        body = json.loads(result["body"])
        assert body["verdict"] is True
        assert body["data"] == {}
        assert "error" not in body

    def test_denied_format(self) -> None:
        resp = BudgetCheckResponse(
            allowed=False,
            status_code=429,
            reason="Monthly budget exceeded (110.0% of $1000)",
            budget_status=BudgetStatus(team="t", user="u"),
            retry_after_seconds=3600,
        )
        result = _build_response(resp)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["verdict"] is False
        assert body["error"] == "Monthly budget exceeded (110.0% of $1000)"
        assert body["data"]["budget_status"]["team"] == "t"
        assert body["data"]["retry_after_seconds"] == 3600

    def test_always_200_even_for_400_status(self) -> None:
        resp = BudgetCheckResponse(allowed=False, status_code=400, reason="Invalid request body")
        result = _build_response(resp)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["verdict"] is False
        assert body["error"] == "Invalid request body"


# ── Property-based tests ─────────────────────────────────────────────────────


class TestPropertyBased:
    @given(
        team=st.text(min_size=1, max_size=50),
        spend=st.decimals(min_value=0, max_value=1_000_000, places=2, allow_nan=False, allow_infinity=False),
        budget=st.decimals(min_value=0, max_value=1_000_000, places=2, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_budget_check_never_crashes(
        self,
        mock_budget: Any,
        mock_usage: Any,
        team: str,
        spend: Decimal,
        budget: Decimal,
    ) -> None:
        """Budget check should never crash regardless of input values."""
        mock_budget.return_value = {
            "monthly_budget_usd": str(budget),
            "warn_threshold_pct": 80,
            "hard_limit_pct": 100,
        }
        mock_usage.return_value = spend

        jwt = _make_jwt({"custom:team": team, "sub": "user1"})
        req = BudgetCheckRequest(jwt_token=jwt)
        result = _check_budget(req)

        assert isinstance(result, BudgetCheckResponse)
        assert isinstance(result.allowed, bool)

    @given(
        body_text=st.text(max_size=500),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_handler_never_crashes_on_random_body(self, body_text: str) -> None:
        """Handler should return a valid Portkey response for any body input."""
        event = {"body": body_text, "isBase64Encoded": False}
        result = handler(event)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "verdict" in body
        assert isinstance(body["verdict"], bool)

    @given(
        rpm=st.integers(min_value=0, max_value=100000),
        tokens_per_day=st.integers(min_value=-1, max_value=100000000),
        monthly_usd=st.decimals(min_value=0, max_value=1_000_000, places=2, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_tier_config_creation_never_crashes(self, rpm: int, tokens_per_day: int, monthly_usd: Decimal) -> None:
        """TierConfig should accept any reasonable values."""
        tc = TierConfig(rpm=rpm, tokens_per_day=tokens_per_day, monthly_usd=monthly_usd)
        assert tc.rpm == rpm
        assert tc.tokens_per_day == tokens_per_day
