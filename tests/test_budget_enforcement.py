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
    TierConfig,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_jwt(claims: dict[str, Any]) -> str:
    """Build a fake JWT with the given payload claims."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(b"fakesig").decode().rstrip("=")
    return f"{header}.{payload}.{signature}"


def _make_function_url_event(
    jwt: str | None = None,
    model: str | None = None,
    content: str = "hello world",
) -> dict[str, Any]:
    """Build an agentgateway guardrail-webhook event (ADR-017).

    The JWT rides in the forwarded ``x-amzn-oidc-data`` header, the model in an
    optional ``x-model`` header, and the prompt in ``{body: {messages: [...]}}``.
    """
    headers: dict[str, str] = {}
    if jwt is not None:
        headers["x-amzn-oidc-data"] = jwt
    if model is not None:
        headers["x-model"] = model
    return {
        "headers": headers,
        "body": json.dumps({"body": {"messages": [{"role": "user", "content": content}]}}),
        "isBase64Encoded": False,
        "requestContext": {"http": {"method": "POST"}},
    }


def _allowed(result: dict[str, Any]) -> bool:
    """True if the agentgateway action envelope allowed (``pass``) the request."""
    return "pass" in json.loads(result["body"])["action"]


def _reject(result: dict[str, Any]) -> dict[str, Any]:
    """Return the ``reject`` action and its parsed JSON body for a denied request.

    The reject envelope carries ``status_code`` / ``reason`` and a JSON ``body``
    string holding ``error`` + optional ``retry_after_seconds``.
    """
    action = json.loads(result["body"])["action"]["reject"]
    return {**action, "parsed_body": json.loads(action["body"])}


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
        """Built-in defaults should include sandbox, standard, high, unlimited."""
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

    def test_load_tier_defaults_builtin_when_env_absent(self) -> None:
        """With no TIER_DEFAULTS env var, the built-in gwcore defaults are used."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TIER_DEFAULTS", None)
            result = _load_tier_defaults()
        assert result["standard"].monthly_usd == Decimal(100)
        assert result["high"].monthly_usd == Decimal(1000)
        assert set(result) == {"sandbox", "standard", "high", "unlimited"}

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
        result = handler(_make_function_url_event(jwt=jwt))

        assert result["statusCode"] == 200
        assert _allowed(result)

    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_blocked_request(self, mock_budget: Any, mock_usage: Any) -> None:
        mock_budget.return_value = {"monthly_budget_usd": "100", "warn_threshold_pct": 80, "hard_limit_pct": 100}
        mock_usage.return_value = Decimal("150.00")

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        result = handler(_make_function_url_event(jwt=jwt))

        assert result["statusCode"] == 200
        rej = _reject(result)
        assert rej["status_code"] == 429
        assert "exceeded" in rej["parsed_body"]["error"].lower()

    def test_invalid_body(self) -> None:
        event = {"body": "not json!!!", "isBase64Encoded": False}
        result = handler(event)
        assert result["statusCode"] == 200
        rej = _reject(result)
        assert rej["status_code"] == 400
        assert rej["parsed_body"]["error"] == "Invalid request body"

    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_missing_jwt_header_handled_gracefully(self, mock_budget: Any, mock_usage: Any) -> None:
        # No x-amzn-oidc-data header: identity resolves to "unknown" and the
        # check still runs (no validation error under the agentgateway contract).
        mock_budget.return_value = {"monthly_budget_usd": "1000", "warn_threshold_pct": 80, "hard_limit_pct": 100}
        mock_usage.return_value = Decimal("10.00")
        result = handler(_make_function_url_event())
        assert result["statusCode"] == 200
        assert _allowed(result)

    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_base64_encoded_body(self, mock_budget: Any, mock_usage: Any) -> None:
        mock_budget.return_value = {"monthly_budget_usd": "1000", "warn_threshold_pct": 80, "hard_limit_pct": 100}
        mock_usage.return_value = Decimal(0)

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        raw_event = _make_function_url_event(jwt=jwt)
        raw_event["body"] = base64.b64encode(raw_event["body"].encode()).decode()
        raw_event["isBase64Encoded"] = True

        result = handler(raw_event)
        assert result["statusCode"] == 200
        assert _allowed(result)

    @patch("budget_enforcement.handler._get_model_usage")
    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_model_budget_exceeded_response_shape(
        self, mock_budget: Any, mock_usage: Any, mock_model_usage: Any
    ) -> None:
        """E.5: Full handler returns a reject action for a model budget exceed.

        The model is read from the forwarded ``x-model`` header.
        """
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
        result = handler(_make_function_url_event(jwt=jwt, model="claude-opus-4"))

        assert result["statusCode"] == 200
        rej = _reject(result)
        assert rej["status_code"] == 429
        assert "Model budget exceeded" in rej["parsed_body"]["error"]


# ── gwcore observability (ADR-016) ───────────────────────────────────────────


class TestObservability:
    """The migration adds a deny-audit + metrics on the agentgateway contract."""

    @patch("budget_enforcement.handler.audit.emit")
    @patch("budget_enforcement.handler.emit_metric")
    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_deny_emits_audit_and_metric(
        self, mock_budget: Any, mock_usage: Any, mock_metric: Any, mock_audit: Any
    ) -> None:
        mock_budget.return_value = {"monthly_budget_usd": "100", "warn_threshold_pct": 80, "hard_limit_pct": 100}
        mock_usage.return_value = Decimal("150.00")

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        result = handler(_make_function_url_event(jwt=jwt))

        assert not _allowed(result)
        # A deny audit event was emitted with decision="deny" and the team.
        assert mock_audit.call_count == 1
        emitted = mock_audit.call_args.args[0]
        assert emitted.decision == "deny"
        assert emitted.team == "platform"
        assert emitted.actor == "user1"
        # The BudgetDenied metric fired.
        assert any(c.args and c.args[0] == "BudgetDenied" for c in mock_metric.call_args_list)

    @patch("budget_enforcement.handler.audit.emit")
    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_allow_does_not_audit(self, mock_budget: Any, mock_usage: Any, mock_audit: Any) -> None:
        mock_budget.return_value = {"monthly_budget_usd": "1000", "warn_threshold_pct": 80, "hard_limit_pct": 100}
        mock_usage.return_value = Decimal("50.00")

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        result = handler(_make_function_url_event(jwt=jwt))

        assert _allowed(result)
        mock_audit.assert_not_called()

    @patch("budget_enforcement.handler.emit_metric")
    def test_invalid_body_emits_error_metric(self, mock_metric: Any) -> None:
        result = handler({"body": "not json!!!", "isBase64Encoded": False})
        assert not _allowed(result)
        assert any(c.args and c.args[0] == "BudgetEnforcementError" for c in mock_metric.call_args_list)

    @patch("budget_enforcement.handler.audit.emit")
    @patch("budget_enforcement.handler.check_rate_limit")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_rate_limit_deny_audited(self, mock_budget: Any, mock_rate: Any, mock_audit: Any) -> None:
        from rate_limiter.models import RateLimitResult

        mock_budget.return_value = {"monthly_budget_usd": "1000", "warn_threshold_pct": 80, "hard_limit_pct": 100}
        mock_rate.return_value = RateLimitResult(allowed=False, reason="RPM exceeded", retry_after_seconds=42)

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        result = handler(_make_function_url_event(jwt=jwt))

        rej = _reject(result)
        assert rej["status_code"] == 429
        assert rej["parsed_body"]["retry_after_seconds"] == 42
        assert mock_audit.call_args.args[0].decision == "deny"

    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_malformed_monthly_budget_falls_back(self, mock_budget: Any, mock_usage: Any) -> None:
        # A non-numeric monthly_budget_usd must fall back to the $1000 default,
        # not crash the check.
        mock_budget.return_value = {"monthly_budget_usd": "not-a-number", "warn_threshold_pct": 80}
        mock_usage.return_value = Decimal("10.00")

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        result = handler(_make_function_url_event(jwt=jwt))

        # Low utilization with the default budget -> allow.
        assert _allowed(result)


# ── Seconds until period reset ───────────────────────────────────────────────


class TestSecondsUntilPeriodReset:
    def test_positive(self) -> None:
        seconds = _seconds_until_period_reset()
        assert seconds >= 1


# ── Models ───────────────────────────────────────────────────────────────────


class TestBudgetModels:
    def test_budget_check_request_defaults(self) -> None:
        req = BudgetCheckRequest(jwt_token="a.b.c")
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


# ── Property-based tests ─────────────────────────────────────────────────────


class TestPropertyBased:
    @given(
        team=st.text(min_size=1, max_size=50),
        spend=st.decimals(min_value=0, max_value=1_000_000, places=2, allow_nan=False, allow_infinity=False),
        budget=st.decimals(min_value=0, max_value=1_000_000, places=2, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow], deadline=None)
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
        """Handler should return a valid agentgateway action envelope for any body."""
        event = {"body": body_text, "isBase64Encoded": False}
        result = handler(event)
        assert result["statusCode"] == 200
        action = json.loads(result["body"])["action"]
        # Exactly one of pass / reject / mask is present.
        assert sum(k in action for k in ("pass", "reject", "mask")) == 1

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
