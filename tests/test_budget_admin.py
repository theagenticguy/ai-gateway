"""Tests for the Budget Admin REST API.

Covers all 7 routes, admin JWT auth (valid, missing, wrong scope),
pagination, validation errors, and 404 responses.
Uses mocked DynamoDB via unittest.mock.
"""

from __future__ import annotations

import base64
import json
from decimal import Decimal
from typing import Any
from unittest.mock import patch

from botocore.exceptions import ClientError

from budget_admin.auth import decode_jwt_payload, validate_admin_scope
from budget_admin.handler import handler
from budget_admin.models import (
    BudgetPeriod,
    BudgetResponse,
    BudgetScope,
    CreateBudgetRequest,
    ListResponse,
    TenantTier,
    UpdateBudgetRequest,
    UsageResponse,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_jwt(claims: dict[str, Any]) -> str:
    """Build a fake JWT with the given payload claims."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(b"fakesig").decode().rstrip("=")
    return f"{header}.{payload}.{signature}"


def _admin_jwt() -> str:
    """Return a JWT with admin scope."""
    return _make_jwt({"sub": "admin-user", "scope": "admin openid"})


def _non_admin_jwt() -> str:
    """Return a JWT without admin scope."""
    return _make_jwt({"sub": "regular-user", "scope": "openid profile"})


def _make_event(
    method: str = "GET",
    path: str = "/budgets",
    body: dict[str, Any] | None = None,
    authorization: str = "",
    query_params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a Lambda Function URL event."""
    event: dict[str, Any] = {
        "requestContext": {"http": {"method": method, "path": path}},
        "rawPath": path,
        "headers": {"authorization": authorization} if authorization else {},
        "isBase64Encoded": False,
    }
    if body is not None:
        event["body"] = json.dumps(body)
    else:
        event["body"] = "{}"
    if query_params:
        event["queryStringParameters"] = query_params
    return event


# ── Auth tests ───────────────────────────────────────────────────────────────


class TestDecodeJwtPayload:
    def test_valid_jwt(self) -> None:
        claims = {"sub": "user-123", "scope": "admin"}
        token = _make_jwt(claims)
        decoded = decode_jwt_payload(token)
        assert decoded["sub"] == "user-123"
        assert decoded["scope"] == "admin"

    def test_empty_string(self) -> None:
        assert decode_jwt_payload("") == {}

    def test_single_part(self) -> None:
        assert decode_jwt_payload("noperiods") == {}

    def test_invalid_base64(self) -> None:
        assert decode_jwt_payload("a.!!!invalid!!!.c") == {}

    def test_non_dict_payload(self) -> None:
        payload = base64.urlsafe_b64encode(json.dumps([1, 2]).encode()).decode()
        assert decode_jwt_payload(f"h.{payload}.s") == {}


class TestValidateAdminScope:
    def test_valid_admin_bearer(self) -> None:
        token = _admin_jwt()
        result = validate_admin_scope(f"Bearer {token}")
        assert result is not None
        assert result["sub"] == "admin-user"

    def test_valid_admin_without_bearer_prefix(self) -> None:
        token = _admin_jwt()
        result = validate_admin_scope(token)
        assert result is not None

    def test_non_admin_scope(self) -> None:
        token = _non_admin_jwt()
        result = validate_admin_scope(f"Bearer {token}")
        assert result is None

    def test_empty_authorization(self) -> None:
        result = validate_admin_scope("")
        assert result is None

    def test_bearer_only(self) -> None:
        result = validate_admin_scope("Bearer ")
        assert result is None

    def test_invalid_jwt(self) -> None:
        result = validate_admin_scope("Bearer not-a-jwt")
        assert result is None

    def test_admin_via_role_claim(self) -> None:
        token = _make_jwt({"sub": "admin", "role": "admin"})
        result = validate_admin_scope(f"Bearer {token}")
        assert result is not None

    def test_admin_via_custom_role_claim(self) -> None:
        token = _make_jwt({"sub": "admin", "custom:role": "Admin"})
        result = validate_admin_scope(f"Bearer {token}")
        assert result is not None

    def test_scope_as_list(self) -> None:
        token = _make_jwt({"sub": "admin", "scope": ["admin", "openid"]})
        result = validate_admin_scope(f"Bearer {token}")
        assert result is not None

    def test_scope_as_list_without_admin(self) -> None:
        token = _make_jwt({"sub": "user", "scope": ["openid", "profile"]})
        result = validate_admin_scope(f"Bearer {token}")
        assert result is None


# ── Handler auth integration ─────────────────────────────────────────────────


class TestHandlerAuth:
    def test_missing_auth_returns_403(self) -> None:
        event = _make_event(method="GET", path="/budgets")
        result = handler(event)
        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert "admin" in body["error"].lower()

    def test_wrong_scope_returns_403(self) -> None:
        event = _make_event(
            method="GET",
            path="/budgets",
            authorization=f"Bearer {_non_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 403

    def test_health_check_no_auth_required(self) -> None:
        event = _make_event(method="GET", path="/health")
        result = handler(event)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["status"] == "healthy"


# ── List budgets ─────────────────────────────────────────────────────────────


class TestListBudgets:
    @patch("budget_admin.routes._budgets_table")
    def test_list_empty(self, mock_table: Any) -> None:
        mock_table.return_value.scan.return_value = {"Items": [], "Count": 0}

        event = _make_event(
            method="GET",
            path="/budgets",
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["items"] == []
        assert body["count"] == 0

    @patch("budget_admin.routes._budgets_table")
    def test_list_with_items(self, mock_table: Any) -> None:
        mock_table.return_value.scan.return_value = {
            "Items": [
                {"budget_id": "b1", "scope": "CONFIG", "budget_usd": Decimal(1000)},
                {"budget_id": "b2", "scope": "CONFIG", "budget_usd": Decimal(500)},
            ],
            "Count": 2,
        }

        event = _make_event(
            method="GET",
            path="/budgets",
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["count"] == 2
        assert len(body["items"]) == 2

    @patch("budget_admin.routes._budgets_table")
    def test_list_with_pagination(self, mock_table: Any) -> None:
        last_key = {"budget_id": "b25", "scope": "CONFIG"}
        mock_table.return_value.scan.return_value = {
            "Items": [{"budget_id": "b1"}],
            "Count": 1,
            "LastEvaluatedKey": last_key,
        }

        event = _make_event(
            method="GET",
            path="/budgets",
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["last_key"] == last_key

    @patch("budget_admin.routes._budgets_table")
    def test_list_with_last_key_param(self, mock_table: Any) -> None:
        mock_table.return_value.scan.return_value = {"Items": [], "Count": 0}

        last_key = json.dumps({"budget_id": "b10", "scope": "CONFIG"})
        event = _make_event(
            method="GET",
            path="/budgets",
            authorization=f"Bearer {_admin_jwt()}",
            query_params={"last_key": last_key},
        )
        result = handler(event)
        assert result["statusCode"] == 200

        # Verify scan was called with ExclusiveStartKey
        call_kwargs = mock_table.return_value.scan.call_args[1]
        assert "ExclusiveStartKey" in call_kwargs

    @patch("budget_admin.routes._budgets_table")
    def test_list_invalid_last_key(self, mock_table: Any) -> None:
        event = _make_event(
            method="GET",
            path="/budgets",
            authorization=f"Bearer {_admin_jwt()}",
            query_params={"last_key": "not-json"},
        )
        result = handler(event)
        assert result["statusCode"] == 400

    @patch("budget_admin.routes._budgets_table")
    def test_list_dynamodb_error(self, mock_table: Any) -> None:
        mock_table.return_value.scan.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "DDB down"}},
            "Scan",
        )

        event = _make_event(
            method="GET",
            path="/budgets",
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 502


# ── Get budget ───────────────────────────────────────────────────────────────


class TestGetBudget:
    @patch("budget_admin.routes._usage_table")
    @patch("budget_admin.routes._budgets_table")
    def test_get_existing_budget(self, mock_budgets: Any, mock_usage: Any) -> None:
        mock_budgets.return_value.get_item.return_value = {
            "Item": {
                "budget_id": "abc-123",
                "scope": "CONFIG",
                "scope_type": "team",
                "scope_id": "platform",
                "budget_usd": Decimal(5000),
                "period": "monthly",
                "tier": "premium",
                "alert_thresholds": [50, 80, 100],
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-03-01T00:00:00",
            }
        }
        mock_usage.return_value.get_item.return_value = {
            "Item": {
                "scope_id": "platform",
                "period_date": "2026-03",
                "total_cost_usd": Decimal("1234.56"),
                "total_tokens": 50000,
            }
        }

        event = _make_event(
            method="GET",
            path="/budgets/abc-123",
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["budget_id"] == "abc-123"
        assert body["current_usage_usd"] == "1234.56"
        assert body["current_tokens"] == 50000

    @patch("budget_admin.routes._budgets_table")
    def test_get_nonexistent_budget(self, mock_table: Any) -> None:
        mock_table.return_value.get_item.return_value = {}

        event = _make_event(
            method="GET",
            path="/budgets/doesnt-exist",
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 404
        body = json.loads(result["body"])
        assert "not found" in body["error"].lower()

    @patch("budget_admin.routes._usage_table")
    @patch("budget_admin.routes._budgets_table")
    def test_get_budget_usage_fetch_fails_gracefully(self, mock_budgets: Any, mock_usage: Any) -> None:
        mock_budgets.return_value.get_item.return_value = {
            "Item": {
                "budget_id": "abc-123",
                "scope": "CONFIG",
                "scope_id": "platform",
                "budget_usd": Decimal(5000),
                "period": "monthly",
                "tier": "standard",
            }
        }
        mock_usage.return_value.get_item.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "DDB down"}},
            "GetItem",
        )

        event = _make_event(
            method="GET",
            path="/budgets/abc-123",
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        # Should still return the budget, just without usage data
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["budget_id"] == "abc-123"


# ── Create budget ────────────────────────────────────────────────────────────


class TestCreateBudget:
    @patch("budget_admin.routes._budgets_table")
    def test_create_valid_budget(self, mock_table: Any) -> None:
        mock_table.return_value.put_item.return_value = {}

        body = {
            "scope": "team",
            "scope_id": "platform-eng",
            "budget_usd": "5000.00",
            "period": "monthly",
            "tier": "premium",
            "alert_thresholds": [50, 80, 100],
        }
        event = _make_event(
            method="POST",
            path="/budgets",
            body=body,
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 201
        resp_body = json.loads(result["body"])
        assert "budget_id" in resp_body
        assert resp_body["message"] == "Budget created"

    @patch("budget_admin.routes._budgets_table")
    def test_create_minimal_budget(self, mock_table: Any) -> None:
        mock_table.return_value.put_item.return_value = {}

        body = {"scope": "user", "scope_id": "user-42", "budget_usd": "100"}
        event = _make_event(
            method="POST",
            path="/budgets",
            body=body,
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 201

    def test_create_missing_required_fields(self) -> None:
        body = {"scope": "team"}  # missing scope_id and budget_usd
        event = _make_event(
            method="POST",
            path="/budgets",
            body=body,
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 400
        body_resp = json.loads(result["body"])
        assert "error" in body_resp

    def test_create_invalid_scope(self) -> None:
        body = {"scope": "invalid", "scope_id": "x", "budget_usd": "100"}
        event = _make_event(
            method="POST",
            path="/budgets",
            body=body,
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 400

    def test_create_negative_budget(self) -> None:
        body = {"scope": "team", "scope_id": "x", "budget_usd": "-100"}
        event = _make_event(
            method="POST",
            path="/budgets",
            body=body,
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 400

    def test_create_invalid_json_body(self) -> None:
        event = _make_event(
            method="POST",
            path="/budgets",
            authorization=f"Bearer {_admin_jwt()}",
        )
        event["body"] = "not valid json!!!"
        result = handler(event)
        assert result["statusCode"] == 400

    @patch("budget_admin.routes._budgets_table")
    def test_create_conflict(self, mock_table: Any) -> None:
        mock_table.return_value.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
            "PutItem",
        )

        body = {"scope": "team", "scope_id": "platform", "budget_usd": "1000"}
        event = _make_event(
            method="POST",
            path="/budgets",
            body=body,
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 409


# ── Update budget ────────────────────────────────────────────────────────────


class TestUpdateBudget:
    @patch("budget_admin.routes._budgets_table")
    def test_update_existing_budget(self, mock_table: Any) -> None:
        mock_table.return_value.update_item.return_value = {
            "Attributes": {"budget_id": "abc-123", "budget_usd": Decimal(7500)}
        }

        body = {"budget_usd": "7500.00"}
        event = _make_event(
            method="PUT",
            path="/budgets/abc-123",
            body=body,
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 200
        resp_body = json.loads(result["body"])
        assert resp_body["message"] == "Budget updated"

    @patch("budget_admin.routes._budgets_table")
    def test_update_multiple_fields(self, mock_table: Any) -> None:
        mock_table.return_value.update_item.return_value = {"Attributes": {}}

        body = {"budget_usd": "3000", "tier": "enterprise", "alert_thresholds": [60, 90, 100]}
        event = _make_event(
            method="PUT",
            path="/budgets/abc-123",
            body=body,
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 200

    def test_update_empty_body(self) -> None:
        event = _make_event(
            method="PUT",
            path="/budgets/abc-123",
            body={},
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "no fields" in body["error"].lower()

    @patch("budget_admin.routes._budgets_table")
    def test_update_nonexistent_budget(self, mock_table: Any) -> None:
        mock_table.return_value.update_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "not found"}},
            "UpdateItem",
        )

        body = {"budget_usd": "500"}
        event = _make_event(
            method="PUT",
            path="/budgets/doesnt-exist",
            body=body,
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 404

    def test_update_invalid_budget_usd(self) -> None:
        body = {"budget_usd": "-100"}
        event = _make_event(
            method="PUT",
            path="/budgets/abc-123",
            body=body,
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 400


# ── Delete budget ────────────────────────────────────────────────────────────


class TestDeleteBudget:
    @patch("budget_admin.routes._budgets_table")
    def test_delete_existing_budget(self, mock_table: Any) -> None:
        mock_table.return_value.delete_item.return_value = {}

        event = _make_event(
            method="DELETE",
            path="/budgets/abc-123",
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "deleted" in body["message"].lower()

    @patch("budget_admin.routes._budgets_table")
    def test_delete_nonexistent_budget(self, mock_table: Any) -> None:
        mock_table.return_value.delete_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "not found"}},
            "DeleteItem",
        )

        event = _make_event(
            method="DELETE",
            path="/budgets/doesnt-exist",
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 404


# ── Get usage ────────────────────────────────────────────────────────────────


class TestGetUsage:
    @patch("budget_admin.routes._usage_table")
    def test_get_existing_usage(self, mock_table: Any) -> None:
        mock_table.return_value.get_item.return_value = {
            "Item": {
                "scope_id": "team#platform",
                "period_date": "2026-03",
                "total_cost_usd": Decimal("2345.67"),
                "total_tokens": 100000,
                "input_tokens": 60000,
                "output_tokens": 40000,
                "cached_tokens": 5000,
                "request_count": 150,
            }
        }

        event = _make_event(
            method="GET",
            path="/usage/team/platform",
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["total_cost_usd"] == "2345.67"
        assert body["total_tokens"] == 100000

    @patch("budget_admin.routes._usage_table")
    def test_get_nonexistent_usage_returns_zeroes(self, mock_table: Any) -> None:
        mock_table.return_value.get_item.return_value = {}

        event = _make_event(
            method="GET",
            path="/usage/user/new-user",
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["total_cost_usd"] == "0.00"
        assert body["total_tokens"] == 0
        assert body["request_count"] == 0


# ── Get usage history ────────────────────────────────────────────────────────


class TestGetUsageHistory:
    @patch("budget_admin.routes._usage_table")
    def test_get_history_with_results(self, mock_table: Any) -> None:
        mock_table.return_value.query.return_value = {
            "Items": [
                {
                    "scope_id": "team#platform",
                    "period_date": "2026-03-01",
                    "total_cost_usd": Decimal(100),
                    "total_tokens": 5000,
                    "input_tokens": 3000,
                    "output_tokens": 2000,
                    "cached_tokens": 0,
                    "request_count": 10,
                },
                {
                    "scope_id": "team#platform",
                    "period_date": "2026-03-02",
                    "total_cost_usd": Decimal(150),
                    "total_tokens": 7500,
                    "input_tokens": 4500,
                    "output_tokens": 3000,
                    "cached_tokens": 500,
                    "request_count": 15,
                },
            ]
        }

        event = _make_event(
            method="GET",
            path="/usage/team/platform/history",
            authorization=f"Bearer {_admin_jwt()}",
            query_params={"start_date": "2026-03-01", "end_date": "2026-03-31"},
        )
        result = handler(event)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["count"] == 2
        assert len(body["items"]) == 2

    @patch("budget_admin.routes._usage_table")
    def test_get_history_empty(self, mock_table: Any) -> None:
        mock_table.return_value.query.return_value = {"Items": []}

        event = _make_event(
            method="GET",
            path="/usage/team/new-team/history",
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["count"] == 0
        assert body["items"] == []

    @patch("budget_admin.routes._usage_table")
    def test_get_history_dynamodb_error(self, mock_table: Any) -> None:
        mock_table.return_value.query.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "DDB down"}},
            "Query",
        )

        event = _make_event(
            method="GET",
            path="/usage/team/platform/history",
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 502


# ── Routing / 404 ───────────────────────────────────────────────────────────


class TestRouting:
    def test_unknown_path_returns_404(self) -> None:
        event = _make_event(
            method="GET",
            path="/unknown/path",
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 404

    def test_wrong_method_returns_404(self) -> None:
        event = _make_event(
            method="PATCH",
            path="/budgets/abc-123",
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 404

    def test_post_to_budgets_detail_returns_404(self) -> None:
        event = _make_event(
            method="POST",
            path="/budgets/abc-123",
            authorization=f"Bearer {_admin_jwt()}",
        )
        result = handler(event)
        assert result["statusCode"] == 404


# ── Model validation tests ──────────────────────────────────────────────────


class TestModels:
    def test_create_budget_request_defaults(self) -> None:
        req = CreateBudgetRequest(scope="team", scope_id="eng", budget_usd=Decimal(1000))
        assert req.period == BudgetPeriod.MONTHLY
        assert req.tier == TenantTier.STANDARD
        assert req.alert_thresholds == [50, 80, 100]
        assert req.model_limits == []
        assert req.token_limit is None

    def test_create_budget_request_all_fields(self) -> None:
        req = CreateBudgetRequest(
            scope="project",
            scope_id="proj-1",
            budget_usd=Decimal(50000),
            token_limit=1_000_000,
            period="quarterly",
            tier="enterprise",
            model_limits=[{"model": "claude-sonnet-4-20250514", "max_cost_usd": Decimal(10000)}],
            alert_thresholds=[25, 50, 75, 100],
        )
        assert req.scope == BudgetScope.PROJECT
        assert req.token_limit == 1_000_000
        assert len(req.model_limits) == 1

    def test_update_budget_request_partial(self) -> None:
        req = UpdateBudgetRequest(budget_usd=Decimal(2000))
        assert req.budget_usd == Decimal(2000)
        assert req.tier is None
        assert req.period is None

    def test_budget_response_serialization(self) -> None:
        resp = BudgetResponse(
            budget_id="b1",
            scope="team",
            scope_id="eng",
            budget_usd=Decimal(5000),
            period="monthly",
            tier="standard",
            current_usage_usd=Decimal("1234.56"),
        )
        dumped = resp.model_dump(exclude_none=True, mode="json")
        assert dumped["budget_id"] == "b1"
        assert dumped["current_usage_usd"] is not None

    def test_usage_response_defaults(self) -> None:
        resp = UsageResponse(scope_id="team#eng", period_date="2026-03")
        assert resp.total_cost_usd == Decimal("0.00")
        assert resp.total_tokens == 0
        assert resp.request_count == 0

    def test_list_response(self) -> None:
        resp = ListResponse(items=[{"a": 1}], count=1)
        assert resp.count == 1
        assert resp.last_key is None

    def test_create_budget_scope_enum(self) -> None:
        assert BudgetScope.TEAM == "team"
        assert BudgetScope.USER == "user"
        assert BudgetScope.PROJECT == "project"

    def test_budget_period_enum(self) -> None:
        assert BudgetPeriod.MONTHLY == "monthly"
        assert BudgetPeriod.QUARTERLY == "quarterly"
        assert BudgetPeriod.ANNUAL == "annual"
