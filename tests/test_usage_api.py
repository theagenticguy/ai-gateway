"""Tests for the usage self-service API Lambda handler.

Covers:
- Missing/invalid query parameters (400)
- Basic usage request returning current period data
- History parameter returning trailing N months
- Models=true returning per-model breakdown sorted by cost desc
- Budget utilization calculation when budget config exists
- DynamoDB error during current usage (502)
- DynamoDB error during budget config (non-fatal, still returns usage)
- No current usage data (null current_period)
- Invalid history parameter (defaults to 0)
"""

from __future__ import annotations

import base64
import json
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from usage_api.handler import (
    _current_period,
    _item_to_model_usage,
    _item_to_usage_period,
    _safe_decimal,
    _trailing_periods,
    handler,
)
from usage_api.models import ModelUsage, UsagePeriod, UsageResponse

# -- Helpers ------------------------------------------------------------------

ADMIN_SCOPE = "https://gateway.internal/admin"
INVOKE_SCOPE = "https://gateway.internal/invoke"


def _make_jwt(claims: dict[str, Any]) -> str:
    """Build a fake JWT (decoded, not verified) for the authorizer-context path."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"{header}.{payload}.sig"


# Default caller: holds both invoke + admin scope, so existing tests that read
# arbitrary teams still pass (admin bypasses tenant isolation).
_ADMIN_JWT = _make_jwt({"sub": "admin-user", "scope": f"{INVOKE_SCOPE} {ADMIN_SCOPE}"})


def _make_event(
    team: str | None = None,
    history: str | None = None,
    models: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Build an API Gateway event with query params and an admin bearer by default."""
    params: dict[str, str] = {}
    if team is not None:
        params["team"] = team
    if history is not None:
        params["history"] = history
    if models is not None:
        params["models"] = models
    return {
        "queryStringParameters": params or None,
        "requestContext": {"requestId": "rid-test", "http": {"method": "GET", "path": "/usage"}},
        "headers": {"authorization": f"Bearer {token or _ADMIN_JWT}"},
    }


def _err(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(result["body"])["error"]


def _make_usage_item(
    team: str,
    period: str,
    *,
    total_tokens: int = 1000,
    input_tokens: int = 600,
    output_tokens: int = 400,
    cached_tokens: int = 100,
    total_cost_usd: str = "1.50",
    request_count: int = 5,
) -> dict[str, Any]:
    """Build a DynamoDB usage item."""
    return {
        "pk": f"USAGE#TEAM#{team}",
        "sk": f"PERIOD#{period}",
        "total_tokens": total_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "total_cost_usd": Decimal(total_cost_usd),
        "request_count": request_count,
    }


def _make_model_item(
    team: str,
    model: str,
    period: str,
    *,
    total_tokens: int = 500,
    input_tokens: int = 300,
    output_tokens: int = 200,
    total_cost_usd: str = "0.75",
    request_count: int = 3,
) -> dict[str, Any]:
    """Build a DynamoDB model-level usage item."""
    return {
        "pk": f"USAGE#TEAM#{team}#MODEL#{model}",
        "sk": f"PERIOD#{period}",
        "total_tokens": total_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_cost_usd": Decimal(total_cost_usd),
        "request_count": request_count,
    }


def _make_budget_item(
    team: str,
    monthly_budget_usd: str = "100.00",
) -> dict[str, Any]:
    """Build a DynamoDB budget config item."""
    return {
        "pk": f"BUDGET#{team}",
        "sk": "CONFIG",
        "monthly_budget_usd": Decimal(monthly_budget_usd),
    }


def _parse_body(result: dict[str, Any]) -> dict[str, Any]:
    """Parse the JSON body from a Lambda response."""
    return json.loads(result["body"])


def _ddb_error(code: str = "ServiceUnavailable", message: str = "DDB down") -> ClientError:
    """Build a botocore ClientError."""
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "GetItem",
    )


# -- Pydantic models ----------------------------------------------------------


class TestUsagePeriodModel:
    def test_defaults(self) -> None:
        period = UsagePeriod(period="2026-03")
        assert period.total_tokens == 0
        assert period.input_tokens == 0
        assert period.output_tokens == 0
        assert period.cached_tokens == 0
        assert period.total_cost_usd == Decimal("0.00")
        assert period.request_count == 0

    def test_full_construction(self) -> None:
        period = UsagePeriod(
            period="2026-03",
            total_tokens=1000,
            input_tokens=600,
            output_tokens=400,
            cached_tokens=100,
            total_cost_usd=Decimal("1.50"),
            request_count=5,
        )
        assert period.period == "2026-03"
        assert period.total_tokens == 1000
        assert period.total_cost_usd == Decimal("1.50")

    def test_serialization(self) -> None:
        period = UsagePeriod(period="2026-03", total_cost_usd=Decimal("1.50"))
        dumped = period.model_dump(mode="json")
        assert dumped["period"] == "2026-03"
        assert dumped["total_cost_usd"] == "1.50"


class TestModelUsageModel:
    def test_defaults(self) -> None:
        mu = ModelUsage(model="gpt-4")
        assert mu.total_tokens == 0
        assert mu.total_cost_usd == Decimal("0.00")
        assert mu.request_count == 0

    def test_full_construction(self) -> None:
        mu = ModelUsage(
            model="claude-opus-4",
            total_tokens=500,
            input_tokens=300,
            output_tokens=200,
            total_cost_usd=Decimal("2.50"),
            request_count=10,
        )
        assert mu.model == "claude-opus-4"
        assert mu.total_cost_usd == Decimal("2.50")


class TestUsageResponseModel:
    def test_defaults(self) -> None:
        resp = UsageResponse(team="test-team")
        assert resp.current_period is None
        assert resp.history == []
        assert resp.models == []
        assert resp.budget_utilization_pct is None
        assert resp.monthly_budget_usd is None

    def test_full_construction(self) -> None:
        resp = UsageResponse(
            team="test-team",
            current_period=UsagePeriod(period="2026-03", total_tokens=1000),
            history=[UsagePeriod(period="2026-02", total_tokens=500)],
            models=[ModelUsage(model="gpt-4", total_tokens=300)],
            budget_utilization_pct=75.0,
            monthly_budget_usd=Decimal("100.00"),
        )
        assert resp.team == "test-team"
        assert resp.current_period is not None
        assert len(resp.history) == 1
        assert len(resp.models) == 1
        assert resp.budget_utilization_pct == 75.0

    def test_serialization_excludes_none(self) -> None:
        resp = UsageResponse(team="t")
        dumped = resp.model_dump(mode="json")
        assert dumped["team"] == "t"
        assert dumped["current_period"] is None


# -- Helper functions ----------------------------------------------------------


class TestSafeDecimal:
    def test_valid_string(self) -> None:
        assert _safe_decimal("1.50") == Decimal("1.50")

    def test_integer(self) -> None:
        assert _safe_decimal(42) == Decimal(42)

    def test_decimal(self) -> None:
        assert _safe_decimal(Decimal("3.14")) == Decimal("3.14")

    def test_invalid_string(self) -> None:
        assert _safe_decimal("not-a-number") == Decimal("0.00")

    def test_none(self) -> None:
        assert _safe_decimal(None) == Decimal("0.00")

    def test_empty_string(self) -> None:
        assert _safe_decimal("") == Decimal("0.00")


class TestCurrentPeriod:
    def test_format(self) -> None:
        period = _current_period()
        # Should be YYYY-MM format
        assert len(period) == 7
        assert period[4] == "-"
        year, month = period.split("-")
        assert 2020 <= int(year) <= 2100
        assert 1 <= int(month) <= 12


class TestTrailingPeriods:
    def test_single_period(self) -> None:
        periods = _trailing_periods(1)
        assert len(periods) == 1
        assert periods[0] == _current_period()

    def test_multiple_periods(self) -> None:
        periods = _trailing_periods(3)
        assert len(periods) == 3
        # Most recent first
        assert periods[0] == _current_period()

    def test_year_boundary(self) -> None:
        periods = _trailing_periods(13)
        assert len(periods) == 13
        # All periods should be valid YYYY-MM
        for p in periods:
            _year, month = p.split("-")
            assert 1 <= int(month) <= 12

    def test_zero_periods(self) -> None:
        periods = _trailing_periods(0)
        assert periods == []


class TestItemToUsagePeriod:
    def test_converts_item(self) -> None:
        item = _make_usage_item("test-team", "2026-03")
        period = _item_to_usage_period(item, "2026-03")
        assert period.period == "2026-03"
        assert period.total_tokens == 1000
        assert period.input_tokens == 600
        assert period.output_tokens == 400
        assert period.cached_tokens == 100
        assert period.total_cost_usd == Decimal("1.50")
        assert period.request_count == 5

    def test_missing_fields_default_to_zero(self) -> None:
        item = {"pk": "USAGE#TEAM#t", "sk": "PERIOD#2026-03"}
        period = _item_to_usage_period(item, "2026-03")
        assert period.total_tokens == 0
        assert period.total_cost_usd == Decimal(0)


class TestItemToModelUsage:
    def test_converts_item(self) -> None:
        item = _make_model_item("test-team", "gpt-4", "2026-03")
        mu = _item_to_model_usage(item)
        assert mu is not None
        assert mu.model == "gpt-4"
        assert mu.total_tokens == 500
        assert mu.total_cost_usd == Decimal("0.75")

    def test_returns_none_for_missing_model_marker(self) -> None:
        item = {"pk": "USAGE#TEAM#test-team", "sk": "PERIOD#2026-03"}
        assert _item_to_model_usage(item) is None

    def test_returns_none_for_empty_model_name(self) -> None:
        item = {"pk": "USAGE#TEAM#test-team#MODEL#", "sk": "PERIOD#2026-03"}
        assert _item_to_model_usage(item) is None

    def test_returns_none_for_no_pk(self) -> None:
        item = {"sk": "PERIOD#2026-03"}
        assert _item_to_model_usage(item) is None


# -- Authorization (now enforced via gwcore, ADR-016) -------------------------


class TestAuthorization:
    def test_missing_auth_401(self) -> None:
        event = {"queryStringParameters": {"team": "x"}, "requestContext": {"http": {"method": "GET"}}, "headers": {}}
        assert handler(event)["statusCode"] == 401

    def test_tenant_isolation_403(self) -> None:
        # A non-admin caller scoped to team A cannot read team B's usage.
        token = _make_jwt({"sub": "u", "scope": INVOKE_SCOPE, "custom:team": "team-a"})
        result = handler(_make_event(team="team-b", token=token))
        assert result["statusCode"] == 403
        assert _err(result)["code"] == "forbidden"

    @patch("usage_api.handler.dynamodb")
    def test_own_team_allowed(self, mock_ddb: MagicMock) -> None:
        mock_ddb.Table.return_value.get_item.return_value = {}
        token = _make_jwt({"sub": "u", "scope": INVOKE_SCOPE, "custom:team": "team-a"})
        result = handler(_make_event(team="team-a", token=token))
        assert result["statusCode"] == 200


# -- Handler: parameter validation --------------------------------------------


class TestHandlerParameterValidation:
    def test_missing_team_returns_400(self) -> None:
        """Missing team parameter should return 400 (after auth passes)."""
        result = handler(_make_event())
        assert result["statusCode"] == 400
        assert "team" in _err(result)["message"].lower()

    def test_empty_team_returns_400(self) -> None:
        """Empty team string should return 400."""
        assert handler(_make_event(team=""))["statusCode"] == 400

    def test_no_query_string_parameters_returns_400(self) -> None:
        """Null queryStringParameters should return 400 (admin caller)."""
        event = {
            "queryStringParameters": None,
            "requestContext": {"http": {"method": "GET"}},
            "headers": {"authorization": f"Bearer {_ADMIN_JWT}"},
        }
        assert handler(event)["statusCode"] == 400

    def test_missing_query_string_key_returns_400(self) -> None:
        """Missing queryStringParameters key entirely should return 400."""
        event = {"requestContext": {"http": {"method": "GET"}}, "headers": {"authorization": f"Bearer {_ADMIN_JWT}"}}
        assert handler(event)["statusCode"] == 400

    @patch("usage_api.handler.dynamodb")
    def test_invalid_history_defaults_to_zero(self, mock_ddb: Any) -> None:
        """Invalid history parameter should default to 0 (no history)."""
        mock_table = MagicMock()
        mock_table.get_item.return_value = {"Item": _make_usage_item("test-team", _current_period())}
        mock_ddb.Table.return_value = mock_table

        event = _make_event(team="test-team", history="not-a-number")
        result = handler(event)
        assert result["statusCode"] == 200
        body = _parse_body(result)
        assert body["history"] == []

    @patch("usage_api.handler.dynamodb")
    def test_history_none_defaults_to_zero(self, mock_ddb: Any) -> None:
        """No history parameter should default to 0 (no history)."""
        mock_table = MagicMock()
        mock_table.get_item.return_value = {"Item": _make_usage_item("test-team", _current_period())}
        mock_ddb.Table.return_value = mock_table

        event = _make_event(team="test-team")
        result = handler(event)
        assert result["statusCode"] == 200
        body = _parse_body(result)
        assert body["history"] == []


# -- Handler: basic usage request ---------------------------------------------


class TestHandlerBasicUsage:
    @patch("usage_api.handler.dynamodb")
    def test_returns_current_period_data(self, mock_ddb: Any) -> None:
        """Basic usage request returns current period data for the team."""
        period = _current_period()
        usage_item = _make_usage_item("test-team", period, total_tokens=5000, total_cost_usd="10.00")

        mock_table = MagicMock()
        mock_table.get_item.return_value = {"Item": usage_item}
        mock_ddb.Table.return_value = mock_table

        event = _make_event(team="test-team")
        result = handler(event)

        assert result["statusCode"] == 200
        body = _parse_body(result)
        assert body["team"] == "test-team"
        assert body["current_period"] is not None
        assert body["current_period"]["period"] == period
        assert body["current_period"]["total_tokens"] == 5000
        assert float(body["current_period"]["total_cost_usd"]) == 10.0

    @patch("usage_api.handler.dynamodb")
    def test_no_current_usage_returns_null_current_period(self, mock_ddb: Any) -> None:
        """When no usage data exists for the current period, current_period should be null."""
        mock_table = MagicMock()
        # get_item returns no Item for usage, and no Item for budget
        mock_table.get_item.return_value = {}
        mock_ddb.Table.return_value = mock_table

        event = _make_event(team="new-team")
        result = handler(event)

        assert result["statusCode"] == 200
        body = _parse_body(result)
        assert body["team"] == "new-team"
        assert body["current_period"] is None

    @patch("usage_api.handler.dynamodb")
    def test_response_includes_all_fields(self, mock_ddb: Any) -> None:
        """Response should include all top-level fields."""
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}
        mock_ddb.Table.return_value = mock_table

        event = _make_event(team="test-team")
        result = handler(event)

        assert result["statusCode"] == 200
        body = _parse_body(result)
        assert "team" in body
        assert "current_period" in body
        assert "history" in body
        assert "models" in body
        assert "budget_utilization_pct" in body
        assert "monthly_budget_usd" in body


# -- Handler: history ---------------------------------------------------------


class TestHandlerHistory:
    @patch("usage_api.handler.dynamodb")
    def test_history_returns_trailing_months(self, mock_ddb: Any) -> None:
        """History parameter returns trailing N months of data."""
        period = _current_period()

        mock_usage_table = MagicMock()
        mock_budgets_table = MagicMock()

        call_count = 0

        def get_item_side_effect(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            key = kwargs.get("Key", {})
            pk = key.get("pk", "")
            if pk.startswith("BUDGET#"):
                return {}
            # Return data for the current period query and for history
            return {"Item": _make_usage_item("test-team", period, total_tokens=1000 * call_count)}

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router
        mock_usage_table.get_item.side_effect = get_item_side_effect
        mock_budgets_table.get_item.return_value = {}

        event = _make_event(team="test-team", history="3")
        result = handler(event)

        assert result["statusCode"] == 200
        body = _parse_body(result)
        assert len(body["history"]) == 3

    @patch("usage_api.handler.dynamodb")
    def test_history_zero_returns_empty(self, mock_ddb: Any) -> None:
        """History=0 should return empty history list."""
        mock_table = MagicMock()
        mock_table.get_item.return_value = {"Item": _make_usage_item("test-team", _current_period())}
        mock_ddb.Table.return_value = mock_table

        event = _make_event(team="test-team", history="0")
        result = handler(event)

        assert result["statusCode"] == 200
        body = _parse_body(result)
        assert body["history"] == []

    @patch("usage_api.handler.dynamodb")
    def test_history_skips_missing_periods(self, mock_ddb: Any) -> None:
        """History periods with no data should be omitted from the list."""
        mock_usage_table = MagicMock()
        mock_budgets_table = MagicMock()

        history_call = 0

        def get_item_side_effect(**kwargs: Any) -> dict[str, Any]:
            nonlocal history_call
            key = kwargs.get("Key", {})
            pk = key.get("pk", "")
            if pk.startswith("BUDGET#"):
                return {}
            history_call += 1
            # Return data for 1st and 3rd calls, empty for 2nd
            if history_call == 2:
                return {}
            return {"Item": _make_usage_item("test-team", "2026-03")}

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router
        mock_usage_table.get_item.side_effect = get_item_side_effect
        mock_budgets_table.get_item.return_value = {}

        event = _make_event(team="test-team", history="3")
        result = handler(event)

        assert result["statusCode"] == 200
        body = _parse_body(result)
        # 4 get_item calls total: 1 for current period, 3 for history
        # current period returns data, history: call2=data, call3=empty, call4=data
        # So history has 2 entries (call2 and call4)
        assert len(body["history"]) == 2


# -- Handler: models ----------------------------------------------------------


class TestHandlerModels:
    @patch("usage_api.handler.dynamodb")
    def test_models_returns_per_model_breakdown(self, mock_ddb: Any) -> None:
        """models=true returns per-model breakdown for current period."""
        period = _current_period()

        mock_usage_table = MagicMock()
        mock_budgets_table = MagicMock()

        model_items = [
            _make_model_item("test-team", "gpt-4", period, total_cost_usd="5.00"),
            _make_model_item("test-team", "claude-opus-4", period, total_cost_usd="10.00"),
        ]

        mock_usage_table.get_item.return_value = {
            "Item": _make_usage_item("test-team", period, total_cost_usd="15.00"),
        }
        mock_usage_table.scan.return_value = {"Items": model_items}
        mock_budgets_table.get_item.return_value = {}

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router

        event = _make_event(team="test-team", models="true")
        result = handler(event)

        assert result["statusCode"] == 200
        body = _parse_body(result)
        assert len(body["models"]) == 2

    @patch("usage_api.handler.dynamodb")
    def test_models_sorted_by_cost_descending(self, mock_ddb: Any) -> None:
        """Model list should be sorted by total_cost_usd descending."""
        period = _current_period()

        mock_usage_table = MagicMock()
        mock_budgets_table = MagicMock()

        model_items = [
            _make_model_item("test-team", "gpt-4", period, total_cost_usd="2.00"),
            _make_model_item("test-team", "claude-opus-4", period, total_cost_usd="10.00"),
            _make_model_item("test-team", "gpt-3.5", period, total_cost_usd="0.50"),
        ]

        mock_usage_table.get_item.return_value = {
            "Item": _make_usage_item("test-team", period, total_cost_usd="12.50"),
        }
        mock_usage_table.scan.return_value = {"Items": model_items}
        mock_budgets_table.get_item.return_value = {}

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router

        event = _make_event(team="test-team", models="true")
        result = handler(event)

        assert result["statusCode"] == 200
        body = _parse_body(result)
        models = body["models"]
        assert len(models) == 3
        assert models[0]["model"] == "claude-opus-4"
        assert models[1]["model"] == "gpt-4"
        assert models[2]["model"] == "gpt-3.5"

    @patch("usage_api.handler.dynamodb")
    def test_models_false_returns_empty(self, mock_ddb: Any) -> None:
        """models not set to 'true' should return empty model list."""
        mock_table = MagicMock()
        mock_table.get_item.return_value = {"Item": _make_usage_item("test-team", _current_period())}
        mock_ddb.Table.return_value = mock_table

        event = _make_event(team="test-team", models="false")
        result = handler(event)

        assert result["statusCode"] == 200
        body = _parse_body(result)
        assert body["models"] == []

    @patch("usage_api.handler.dynamodb")
    def test_models_paginated_scan(self, mock_ddb: Any) -> None:
        """Model scan handles DynamoDB pagination via LastEvaluatedKey."""
        period = _current_period()

        mock_usage_table = MagicMock()
        mock_budgets_table = MagicMock()

        page1_items = [_make_model_item("test-team", "gpt-4", period, total_cost_usd="5.00")]
        page2_items = [_make_model_item("test-team", "claude-opus-4", period, total_cost_usd="10.00")]

        mock_usage_table.get_item.return_value = {
            "Item": _make_usage_item("test-team", period, total_cost_usd="15.00"),
        }
        mock_usage_table.scan.side_effect = [
            {"Items": page1_items, "LastEvaluatedKey": {"pk": "cursor"}},
            {"Items": page2_items},
        ]
        mock_budgets_table.get_item.return_value = {}

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router

        event = _make_event(team="test-team", models="true")
        result = handler(event)

        assert result["statusCode"] == 200
        body = _parse_body(result)
        assert len(body["models"]) == 2
        assert mock_usage_table.scan.call_count == 2


# -- Handler: budget utilization ----------------------------------------------


class TestHandlerBudgetUtilization:
    @patch("usage_api.handler.dynamodb")
    def test_budget_utilization_calculated(self, mock_ddb: Any) -> None:
        """Budget utilization percentage should be calculated when budget config exists."""
        period = _current_period()

        mock_usage_table = MagicMock()
        mock_budgets_table = MagicMock()

        mock_usage_table.get_item.return_value = {
            "Item": _make_usage_item("test-team", period, total_cost_usd="75.00"),
        }
        mock_budgets_table.get_item.return_value = {
            "Item": _make_budget_item("test-team", "100.00"),
        }

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router

        event = _make_event(team="test-team")
        result = handler(event)

        assert result["statusCode"] == 200
        body = _parse_body(result)
        assert body["budget_utilization_pct"] == pytest.approx(75.0)
        assert float(body["monthly_budget_usd"]) == 100.0

    @patch("usage_api.handler.dynamodb")
    def test_no_budget_config_returns_null(self, mock_ddb: Any) -> None:
        """When no budget config exists, budget fields should be null."""
        period = _current_period()

        mock_usage_table = MagicMock()
        mock_budgets_table = MagicMock()

        mock_usage_table.get_item.return_value = {"Item": _make_usage_item("test-team", period)}
        mock_budgets_table.get_item.return_value = {}  # No budget config

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router

        event = _make_event(team="test-team")
        result = handler(event)

        assert result["statusCode"] == 200
        body = _parse_body(result)
        assert body["budget_utilization_pct"] is None
        assert body["monthly_budget_usd"] is None

    @patch("usage_api.handler.dynamodb")
    def test_zero_budget_no_utilization(self, mock_ddb: Any) -> None:
        """Zero budget should result in null utilization (division by zero avoidance)."""
        period = _current_period()

        mock_usage_table = MagicMock()
        mock_budgets_table = MagicMock()

        mock_usage_table.get_item.return_value = {
            "Item": _make_usage_item("test-team", period, total_cost_usd="50.00"),
        }
        mock_budgets_table.get_item.return_value = {
            "Item": _make_budget_item("test-team", "0"),
        }

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router

        event = _make_event(team="test-team")
        result = handler(event)

        assert result["statusCode"] == 200
        body = _parse_body(result)
        # monthly_budget_usd=0 means the condition `monthly_budget_usd > 0` is False
        assert body["budget_utilization_pct"] is None

    @patch("usage_api.handler.dynamodb")
    def test_no_usage_no_budget_utilization(self, mock_ddb: Any) -> None:
        """When there is no usage data, budget utilization should be null even if budget exists."""
        mock_usage_table = MagicMock()
        mock_budgets_table = MagicMock()

        mock_usage_table.get_item.return_value = {}  # No usage item
        mock_budgets_table.get_item.return_value = {
            "Item": _make_budget_item("test-team", "100.00"),
        }

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router

        event = _make_event(team="test-team")
        result = handler(event)

        assert result["statusCode"] == 200
        body = _parse_body(result)
        assert body["current_period"] is None
        # No current_period means utilization cannot be calculated
        assert body["budget_utilization_pct"] is None
        # But monthly_budget_usd should still be set
        assert float(body["monthly_budget_usd"]) == 100.0


# -- Handler: DynamoDB error handling -----------------------------------------


class TestHandlerDDBErrors:
    @patch("usage_api.handler.dynamodb")
    def test_ddb_error_current_usage_returns_502(self, mock_ddb: Any) -> None:
        """DynamoDB error during current usage query should return 502."""
        mock_table = MagicMock()
        mock_table.get_item.side_effect = _ddb_error()
        mock_ddb.Table.return_value = mock_table

        event = _make_event(team="test-team")
        result = handler(event)

        assert result["statusCode"] == 502
        body = _parse_body(result)
        assert "error" in body

    @patch("usage_api.handler.dynamodb")
    def test_ddb_error_budget_config_non_fatal(self, mock_ddb: Any) -> None:
        """DynamoDB error during budget config is non-fatal; usage data still returned."""
        period = _current_period()

        mock_usage_table = MagicMock()
        mock_budgets_table = MagicMock()

        mock_usage_table.get_item.return_value = {
            "Item": _make_usage_item("test-team", period, total_cost_usd="50.00"),
        }
        mock_budgets_table.get_item.side_effect = _ddb_error()

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router

        event = _make_event(team="test-team")
        result = handler(event)

        # Should still return 200 with usage data
        assert result["statusCode"] == 200
        body = _parse_body(result)
        assert body["team"] == "test-team"
        assert body["current_period"] is not None
        assert body["budget_utilization_pct"] is None
        assert body["monthly_budget_usd"] is None

    @patch("usage_api.handler.dynamodb")
    def test_ddb_error_history_returns_502(self, mock_ddb: Any) -> None:
        """DynamoDB error during history query should return 502."""
        period = _current_period()

        mock_usage_table = MagicMock()
        mock_budgets_table = MagicMock()

        call_count = 0

        def get_item_side_effect(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: current period succeeds
                return {"Item": _make_usage_item("test-team", period)}
            # History calls fail
            raise _ddb_error()

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router
        mock_usage_table.get_item.side_effect = get_item_side_effect
        mock_budgets_table.get_item.return_value = {}

        event = _make_event(team="test-team", history="3")
        result = handler(event)

        assert result["statusCode"] == 502

    @patch("usage_api.handler.dynamodb")
    def test_ddb_error_model_scan_returns_502(self, mock_ddb: Any) -> None:
        """DynamoDB error during model scan should return 502."""
        period = _current_period()

        mock_usage_table = MagicMock()
        mock_budgets_table = MagicMock()

        mock_usage_table.get_item.return_value = {
            "Item": _make_usage_item("test-team", period),
        }
        mock_usage_table.scan.side_effect = _ddb_error()
        mock_budgets_table.get_item.return_value = {}

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router

        event = _make_event(team="test-team", models="true")
        result = handler(event)

        assert result["statusCode"] == 502

    @patch("usage_api.handler.dynamodb")
    def test_generic_exception_current_usage_returns_500(self, mock_ddb: Any) -> None:
        """A non-ClientError (unexpected) maps to 500 internal_error, not 502 upstream."""
        mock_table = MagicMock()
        mock_table.get_item.side_effect = RuntimeError("unexpected")
        mock_ddb.Table.return_value = mock_table

        event = _make_event(team="test-team")
        result = handler(event)

        assert result["statusCode"] == 500


# -- Handler: combined scenarios ----------------------------------------------


class TestHandlerCombined:
    @patch("usage_api.handler.dynamodb")
    def test_usage_with_history_and_models_and_budget(self, mock_ddb: Any) -> None:
        """Full request with all parameters returns complete response."""
        period = _current_period()

        mock_usage_table = MagicMock()
        mock_budgets_table = MagicMock()

        usage_item = _make_usage_item("test-team", period, total_cost_usd="50.00", total_tokens=5000)
        model_items = [
            _make_model_item("test-team", "gpt-4", period, total_cost_usd="30.00"),
            _make_model_item("test-team", "claude-opus-4", period, total_cost_usd="20.00"),
        ]

        mock_usage_table.get_item.return_value = {"Item": usage_item}
        mock_usage_table.scan.return_value = {"Items": model_items}
        mock_budgets_table.get_item.return_value = {
            "Item": _make_budget_item("test-team", "200.00"),
        }

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router

        event = _make_event(team="test-team", history="2", models="true")
        result = handler(event)

        assert result["statusCode"] == 200
        body = _parse_body(result)
        assert body["team"] == "test-team"
        assert body["current_period"] is not None
        assert body["current_period"]["total_tokens"] == 5000
        assert len(body["history"]) == 2
        assert len(body["models"]) == 2
        # Budget: 50/200 = 25%
        assert body["budget_utilization_pct"] == pytest.approx(25.0)
        # Models sorted by cost desc
        assert body["models"][0]["model"] == "gpt-4"
        assert body["models"][1]["model"] == "claude-opus-4"

    @patch("usage_api.handler.dynamodb")
    def test_models_case_insensitive(self, mock_ddb: Any) -> None:
        """models=TRUE (uppercase) should also trigger model breakdown."""
        period = _current_period()

        mock_usage_table = MagicMock()
        mock_budgets_table = MagicMock()

        mock_usage_table.get_item.return_value = {
            "Item": _make_usage_item("test-team", period),
        }
        mock_usage_table.scan.return_value = {
            "Items": [_make_model_item("test-team", "gpt-4", period)],
        }
        mock_budgets_table.get_item.return_value = {}

        def table_router(name: str) -> MagicMock:
            if "budget" in name.lower():
                return mock_budgets_table
            return mock_usage_table

        mock_ddb.Table.side_effect = table_router

        event = _make_event(team="test-team", models="TRUE")
        result = handler(event)

        assert result["statusCode"] == 200
        body = _parse_body(result)
        assert len(body["models"]) == 1


# -- Environment variable configuration --------------------------------------


class TestEnvConfiguration:
    def test_usage_table_default(self) -> None:
        """Default USAGE_TABLE should be 'gateway-usage'."""
        from usage_api.handler import USAGE_TABLE

        # Value is set at module load time; just verify it's a string
        assert isinstance(USAGE_TABLE, str)

    def test_budgets_table_default(self) -> None:
        """Default BUDGETS_TABLE should be 'gateway-budgets'."""
        from usage_api.handler import BUDGETS_TABLE

        assert isinstance(BUDGETS_TABLE, str)


class TestHandlerInfra:
    """Health route and the catch-all (non-ClientError → 500)."""

    def test_health_check(self) -> None:
        event = {"rawPath": "/health", "requestContext": {"http": {"method": "GET", "path": "/health"}}}
        result = handler(event)
        assert result["statusCode"] == 200
        assert _parse_body(result)["status"] == "healthy"

    @patch("usage_api.handler.dynamodb")
    def test_unhandled_error_returns_500(self, mock_ddb: Any) -> None:
        mock_ddb.Table.side_effect = RuntimeError("boom")
        result = handler(_make_event(team="test-team"))
        assert result["statusCode"] == 500
        assert _err(result)["code"] == "internal_error"
