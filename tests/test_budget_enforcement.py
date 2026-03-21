"""Tests for the budget enforcement Lambda.

Covers JWT extraction, budget checking (within budget, at threshold, exceeded),
tier default fallback, and DynamoDB failure graceful degradation.
Uses hypothesis for property-based tests.
"""

from __future__ import annotations

import base64
import json
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from budget_enforcement.handler import (
    _build_response,
    _check_budget,
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
from budget_enforcement.models import BudgetCheckRequest, BudgetCheckResponse, BudgetStatus

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

        jwt = _make_jwt({"custom:team": "new-team", "sub": "user1", "custom:tenant_tier": "free"})
        req = BudgetCheckRequest(jwt_token=jwt)
        result = _check_budget(req)

        assert result.allowed is True
        assert result.budget_status is not None
        assert result.budget_status.monthly_budget_usd == Decimal(10)

    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_tier_default_exceeded(self, mock_budget: Any, mock_usage: Any) -> None:
        """Free tier budget exceeded uses tier defaults."""
        mock_budget.return_value = None
        mock_usage.return_value = Decimal("15.00")  # Over the $10 free tier

        jwt = _make_jwt({"custom:team": "free-team", "sub": "user1", "custom:tenant_tier": "free"})
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
        assert body["allowed"] is True

    @patch("budget_enforcement.handler._get_current_usage")
    @patch("budget_enforcement.handler._get_budget_record")
    def test_blocked_request(self, mock_budget: Any, mock_usage: Any) -> None:
        mock_budget.return_value = {"monthly_budget_usd": "100", "warn_threshold_pct": 80, "hard_limit_pct": 100}
        mock_usage.return_value = Decimal("150.00")

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        event = _make_function_url_event({"jwt_token": jwt})
        result = handler(event)

        assert result["statusCode"] == 429
        body = json.loads(result["body"])
        assert body["allowed"] is False

    def test_invalid_body(self) -> None:
        event = {"body": "not json!!!", "isBase64Encoded": False}
        result = handler(event)
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert body["allowed"] is False

    def test_missing_jwt_token(self) -> None:
        event = _make_function_url_event({"model": "gpt-4"})
        result = handler(event)
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert body["allowed"] is False

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

    def test_budget_status_defaults(self) -> None:
        status = BudgetStatus(team="t", user="u")
        assert status.utilization_pct == 0.0
        assert status.warn_threshold_pct == 80.0
        assert status.hard_limit_pct == 100.0


# ── Build response ───────────────────────────────────────────────────────────


class TestBuildResponse:
    def test_format(self) -> None:
        resp = BudgetCheckResponse(allowed=True)
        result = _build_response(resp)
        assert result["statusCode"] == 200
        assert result["headers"]["Content-Type"] == "application/json"
        body = json.loads(result["body"])
        assert body["allowed"] is True


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
        """Handler should return a valid response for any body input."""
        event = {"body": body_text, "isBase64Encoded": False}
        result = handler(event)
        assert "statusCode" in result
        assert result["statusCode"] in (200, 400, 429)
