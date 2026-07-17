"""Tests for the Budget Admin REST API (migrated onto gwcore, ADR-016).

Covers all 7 routes, real in-handler authorization (admin allowed, non-admin
403, missing auth 401 — previously NOT enforced), gwcore cursor pagination,
the gwcore error envelope, validation errors, audit emission on mutations,
and 404 routing.
"""

from __future__ import annotations

import base64
import json
import os
from decimal import Decimal
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

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
from gwcore.responses import encode_cursor

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_jwt(claims: dict[str, Any]) -> str:
    """Build a fake JWT with the given payload claims (decoded, not verified)."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(b"fakesig").decode().rstrip("=")
    return f"{header}.{payload}.{signature}"


def _admin_jwt() -> str:
    """A JWT carrying the legacy admin scope (accepted via gwcore alias)."""
    return _make_jwt({"sub": "admin-user", "scope": "admin openid"})


def _non_admin_jwt() -> str:
    return _make_jwt({"sub": "regular-user", "scope": "openid profile"})


def _team_jwt(team: str) -> str:
    """A non-admin JWT carrying a specific team claim (custom:team)."""
    return _make_jwt({"sub": "team-user", "scope": "openid profile", "custom:team": team})


def _athena_result_set(rows: list[dict[str, str]]) -> dict[str, Any]:
    """Build an Athena GetQueryResults ResultSet: header row + data rows.

    Column order matches the projection in audit_query._build_query.
    """
    columns = [
        "action",
        "actor",
        "resource",
        "decision",
        "status",
        "team",
        "source_ip",
        "correlation_id",
        "detail",
        "ts",
    ]
    header_row: dict[str, Any] = {"Data": [{"VarCharValue": c} for c in columns]}
    data_rows: list[dict[str, Any]] = [{"Data": [{"VarCharValue": row.get(c, "")} for c in columns]} for row in rows]
    return {"Rows": [header_row, *data_rows]}


def _mock_athena_client(rows: list[dict[str, str]]) -> MagicMock:
    """A MagicMock Athena client that returns a SUCCEEDED query with ``rows``."""
    client = MagicMock()
    client.start_query_execution.return_value = {"QueryExecutionId": "qid-test"}
    client.get_query_execution.return_value = {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}
    client.get_query_results.return_value = {"ResultSet": _athena_result_set(rows)}
    return client


def _make_event(
    method: str = "GET",
    path: str = "/budgets",
    body: dict[str, Any] | None = None,
    authorization: str | None = None,
    query_params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build an API Gateway request event. Defaults to an admin bearer."""
    if authorization is None:
        authorization = f"Bearer {_admin_jwt()}"
    event: dict[str, Any] = {
        "requestContext": {"requestId": "rid-test", "http": {"method": method, "path": path}},
        "rawPath": path,
        "headers": {"authorization": authorization} if authorization else {},
        "isBase64Encoded": False,
        "body": json.dumps(body) if body is not None else "{}",
    }
    if query_params:
        event["queryStringParameters"] = query_params
    return event


def _err(result: dict[str, Any]) -> dict[str, Any]:
    """Extract the gwcore error envelope: body['error'] is {code, message, ...}."""
    return json.loads(result["body"])["error"]


# ── Authorization (the security fix — previously NOT enforced) ─────────────────


class TestAuthorization:
    def test_health_check_no_auth_required(self) -> None:
        result = handler(_make_event(method="GET", path="/health", authorization=""))
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["status"] == "healthy"

    def test_missing_auth_rejected_401(self) -> None:
        result = handler(_make_event(method="GET", path="/budgets", authorization=""))
        assert result["statusCode"] == 401
        assert _err(result)["code"] == "unauthorized"

    def test_non_admin_rejected_403(self) -> None:
        result = handler(_make_event(method="GET", path="/budgets", authorization=f"Bearer {_non_admin_jwt()}"))
        assert result["statusCode"] == 403
        assert _err(result)["code"] == "forbidden"

    @patch("budget_admin.routes._budgets_table")
    def test_canonical_admin_scope_accepted(self, mock_table: Any) -> None:
        mock_table.return_value.scan.return_value = {"Items": [], "Count": 0}
        token = _make_jwt({"sub": "u", "scope": "https://gateway.internal/admin"})
        result = handler(_make_event(method="GET", path="/budgets", authorization=f"Bearer {token}"))
        assert result["statusCode"] == 200

    @patch("budget_admin.handler.audit.emit")
    def test_denial_emits_audit_event(self, mock_audit: Any) -> None:
        # A 403 (non-admin) must be audited as a deny decision (ADR-016).
        result = handler(_make_event(method="GET", path="/budgets", authorization=f"Bearer {_non_admin_jwt()}"))
        assert result["statusCode"] == 403
        mock_audit.assert_called_once()
        ev = mock_audit.call_args[0][0]
        assert ev.decision == "deny"
        assert ev.actor == "regular-user"  # actor derived from the token, not "unknown"


# ── List budgets (gwcore cursor pagination) ────────────────────────────────────


class TestListBudgets:
    @patch("budget_admin.routes._budgets_table")
    def test_list_empty(self, mock_table: Any) -> None:
        mock_table.return_value.scan.return_value = {"Items": [], "Count": 0}
        result = handler(_make_event(method="GET", path="/budgets"))
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["items"] == []
        assert body["count"] == 0
        assert body["next_cursor"] is None

    @patch("budget_admin.routes._budgets_table")
    def test_list_with_items(self, mock_table: Any) -> None:
        mock_table.return_value.scan.return_value = {
            "Items": [
                {"budget_id": "b1", "scope": "CONFIG", "budget_usd": Decimal(1000)},
                {"budget_id": "b2", "scope": "CONFIG", "budget_usd": Decimal(500)},
            ],
            "Count": 2,
        }
        result = handler(_make_event(method="GET", path="/budgets"))
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["count"] == 2
        assert len(body["items"]) == 2

    @patch("budget_admin.routes._budgets_table")
    def test_list_emits_next_cursor(self, mock_table: Any) -> None:
        last_key = {"budget_id": "b25", "scope": "CONFIG"}
        mock_table.return_value.scan.return_value = {
            "Items": [{"budget_id": "b1"}],
            "Count": 1,
            "LastEvaluatedKey": last_key,
        }
        result = handler(_make_event(method="GET", path="/budgets"))
        body = json.loads(result["body"])
        # next_cursor is opaque; it must round-trip back to the DynamoDB key.
        assert body["next_cursor"] == encode_cursor(last_key)

    @patch("budget_admin.routes._budgets_table")
    def test_list_consumes_cursor_param(self, mock_table: Any) -> None:
        mock_table.return_value.scan.return_value = {"Items": [], "Count": 0}
        cursor = encode_cursor({"budget_id": "b10", "scope": "CONFIG"})
        result = handler(_make_event(method="GET", path="/budgets", query_params={"cursor": cursor}))
        assert result["statusCode"] == 200
        call_kwargs = mock_table.return_value.scan.call_args[1]
        assert call_kwargs["ExclusiveStartKey"] == {"budget_id": "b10", "scope": "CONFIG"}

    def test_list_invalid_cursor_400(self) -> None:
        result = handler(_make_event(method="GET", path="/budgets", query_params={"cursor": "!!!not-base64!!!"}))
        assert result["statusCode"] == 400
        assert _err(result)["code"] == "validation_failed"

    @patch("budget_admin.routes._budgets_table")
    def test_list_dynamodb_error_502(self, mock_table: Any) -> None:
        mock_table.return_value.scan.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "DDB down"}}, "Scan"
        )
        result = handler(_make_event(method="GET", path="/budgets"))
        assert result["statusCode"] == 502
        assert _err(result)["code"] == "upstream_error"


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
                "tier": "high",
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
        result = handler(_make_event(method="GET", path="/budgets/abc-123"))
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["budget_id"] == "abc-123"
        assert body["current_usage_usd"] == "1234.56"
        assert body["current_tokens"] == 50000

    @patch("budget_admin.routes._budgets_table")
    def test_get_nonexistent_budget_404(self, mock_table: Any) -> None:
        mock_table.return_value.get_item.return_value = {}
        result = handler(_make_event(method="GET", path="/budgets/doesnt-exist"))
        assert result["statusCode"] == 404
        assert "not found" in _err(result)["message"].lower()

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
            {"Error": {"Code": "InternalServerError", "Message": "DDB down"}}, "GetItem"
        )
        result = handler(_make_event(method="GET", path="/budgets/abc-123"))
        assert result["statusCode"] == 200  # budget still returned, usage omitted
        assert json.loads(result["body"])["budget_id"] == "abc-123"


# ── Create budget (audited) ────────────────────────────────────────────────────


class TestCreateBudget:
    @patch("budget_admin.routes.audit.emit")
    @patch("budget_admin.routes._budgets_table")
    def test_create_valid_budget_emits_audit(self, mock_table: Any, mock_audit: Any) -> None:
        mock_table.return_value.put_item.return_value = {}
        body = {
            "scope": "team",
            "scope_id": "platform-eng",
            "budget_usd": "5000.00",
            "period": "monthly",
            "tier": "high",
            "alert_thresholds": [50, 80, 100],
        }
        result = handler(_make_event(method="POST", path="/budgets", body=body))
        assert result["statusCode"] == 201
        resp_body = json.loads(result["body"])
        assert "budget_id" in resp_body
        assert resp_body["message"] == "Budget created"
        mock_audit.assert_called_once()  # mutation audited

    @patch("budget_admin.routes.audit.emit")
    @patch("budget_admin.routes._budgets_table")
    def test_create_minimal_budget(self, mock_table: Any, _audit: Any) -> None:
        mock_table.return_value.put_item.return_value = {}
        body = {"scope": "user", "scope_id": "user-42", "budget_usd": "100"}
        result = handler(_make_event(method="POST", path="/budgets", body=body))
        assert result["statusCode"] == 201

    def test_create_missing_required_fields_400(self) -> None:
        result = handler(_make_event(method="POST", path="/budgets", body={"scope": "team"}))
        assert result["statusCode"] == 400
        assert _err(result)["code"] == "validation_failed"

    def test_create_invalid_scope_400(self) -> None:
        body = {"scope": "invalid", "scope_id": "x", "budget_usd": "100"}
        result = handler(_make_event(method="POST", path="/budgets", body=body))
        assert result["statusCode"] == 400

    def test_create_negative_budget_400(self) -> None:
        body = {"scope": "team", "scope_id": "x", "budget_usd": "-100"}
        result = handler(_make_event(method="POST", path="/budgets", body=body))
        assert result["statusCode"] == 400

    def test_create_invalid_json_body_400(self) -> None:
        event = _make_event(method="POST", path="/budgets")
        event["body"] = "not valid json!!!"
        result = handler(event)
        assert result["statusCode"] == 400

    @patch("budget_admin.routes._budgets_table")
    def test_create_conflict_409(self, mock_table: Any) -> None:
        mock_table.return_value.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}}, "PutItem"
        )
        body = {"scope": "team", "scope_id": "platform", "budget_usd": "1000"}
        result = handler(_make_event(method="POST", path="/budgets", body=body))
        assert result["statusCode"] == 409
        assert _err(result)["code"] == "conflict"


# ── Update budget (audited) ────────────────────────────────────────────────────


class TestUpdateBudget:
    @patch("budget_admin.routes.audit.emit")
    @patch("budget_admin.routes._budgets_table")
    def test_update_existing_budget_emits_audit(self, mock_table: Any, mock_audit: Any) -> None:
        mock_table.return_value.update_item.return_value = {
            "Attributes": {"budget_id": "abc-123", "budget_usd": Decimal(7500)}
        }
        result = handler(_make_event(method="PUT", path="/budgets/abc-123", body={"budget_usd": "7500.00"}))
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["message"] == "Budget updated"
        mock_audit.assert_called_once()

    @patch("budget_admin.routes.audit.emit")
    @patch("budget_admin.routes._budgets_table")
    def test_update_multiple_fields(self, mock_table: Any, _audit: Any) -> None:
        mock_table.return_value.update_item.return_value = {"Attributes": {}}
        body = {"budget_usd": "3000", "tier": "unlimited", "alert_thresholds": [60, 90, 100]}
        result = handler(_make_event(method="PUT", path="/budgets/abc-123", body=body))
        assert result["statusCode"] == 200

    def test_update_empty_body_400(self) -> None:
        result = handler(_make_event(method="PUT", path="/budgets/abc-123", body={}))
        assert result["statusCode"] == 400
        assert "no fields" in _err(result)["message"].lower()

    @patch("budget_admin.routes._budgets_table")
    def test_update_nonexistent_budget_404(self, mock_table: Any) -> None:
        mock_table.return_value.update_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "not found"}}, "UpdateItem"
        )
        result = handler(_make_event(method="PUT", path="/budgets/doesnt-exist", body={"budget_usd": "500"}))
        assert result["statusCode"] == 404

    def test_update_invalid_budget_usd_400(self) -> None:
        result = handler(_make_event(method="PUT", path="/budgets/abc-123", body={"budget_usd": "-100"}))
        assert result["statusCode"] == 400


# ── Delete budget (audited) ────────────────────────────────────────────────────


class TestDeleteBudget:
    @patch("budget_admin.routes.audit.emit")
    @patch("budget_admin.routes._budgets_table")
    def test_delete_existing_budget_emits_audit(self, mock_table: Any, mock_audit: Any) -> None:
        mock_table.return_value.delete_item.return_value = {}
        result = handler(_make_event(method="DELETE", path="/budgets/abc-123"))
        assert result["statusCode"] == 200
        assert "deleted" in json.loads(result["body"])["message"].lower()
        mock_audit.assert_called_once()

    @patch("budget_admin.routes._budgets_table")
    def test_delete_nonexistent_budget_404(self, mock_table: Any) -> None:
        mock_table.return_value.delete_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "not found"}}, "DeleteItem"
        )
        result = handler(_make_event(method="DELETE", path="/budgets/doesnt-exist"))
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
        result = handler(_make_event(method="GET", path="/usage/team/platform"))
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["total_cost_usd"] == "2345.67"
        assert body["total_tokens"] == 100000

    @patch("budget_admin.routes._usage_table")
    def test_get_nonexistent_usage_returns_zeroes(self, mock_table: Any) -> None:
        mock_table.return_value.get_item.return_value = {}
        result = handler(_make_event(method="GET", path="/usage/user/new-user"))
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
        result = handler(_make_event(method="GET", path="/usage/team/new-team/history"))
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["count"] == 0
        assert body["items"] == []

    @patch("budget_admin.routes._usage_table")
    def test_get_history_dynamodb_error_502(self, mock_table: Any) -> None:
        mock_table.return_value.query.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "DDB down"}}, "Query"
        )
        result = handler(_make_event(method="GET", path="/usage/team/platform/history"))
        assert result["statusCode"] == 502


# ── Routing / 404 ───────────────────────────────────────────────────────────


class TestRouting:
    def test_unknown_path_returns_404(self) -> None:
        result = handler(_make_event(method="GET", path="/unknown/path"))
        assert result["statusCode"] == 404

    def test_wrong_method_returns_404(self) -> None:
        result = handler(_make_event(method="PATCH", path="/budgets/abc-123"))
        assert result["statusCode"] == 404

    def test_post_to_budgets_detail_returns_404(self) -> None:
        result = handler(_make_event(method="POST", path="/budgets/abc-123"))
        assert result["statusCode"] == 404


# ── Audit read (GET /audit) — Athena client mocked, NO live AWS ────────────────


class TestAuditRead:
    _ENV: ClassVar[dict[str, str]] = {
        "AUDIT_ATHENA_WORKGROUP": "gateway-test-audit",
        "AUDIT_ATHENA_CATALOG": "s3tablescatalog/b",
        "AUDIT_ATHENA_DATABASE": "control_plane",
    }

    def _audit_event(self, team: str, authorization: str | None = None) -> dict[str, Any]:
        return _make_event(
            method="GET",
            path="/audit",
            authorization=authorization,
            query_params={"team": team, "start": "2026-06-01T00:00:00Z", "end": "2026-06-30T23:59:59Z"},
        )

    @patch.dict("os.environ", _ENV, clear=False)
    @patch("budget_admin.audit_query._client")
    def test_admin_reads_any_team_happy_path(self, mock_client: Any) -> None:
        # An admin reads a team they are not a member of; happy path → page() envelope.
        mock_client.return_value = _mock_athena_client(
            [
                {
                    "action": "team.update",
                    "actor": "admin-user",
                    "resource": "team/platform",
                    "decision": "allow",
                    "status": "200",
                    "team": "platform",
                    "source_ip": "10.0.0.1",
                    "correlation_id": "rid-1",
                    "detail": "",
                    "ts": "2026-06-15T12:00:00+00:00",
                }
            ]
        )
        result = handler(self._audit_event("platform"))
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        # page() envelope: items / count / next_cursor
        assert body["count"] == 1
        assert body["next_cursor"] is None
        assert body["items"][0]["action"] == "team.update"
        assert body["items"][0]["decision"] == "allow"
        assert body["items"][0]["status"] == 200  # coerced to int
        # team was bound as an ExecutionParameter, never interpolated into SQL.
        call = mock_client.return_value.start_query_execution.call_args
        assert call.kwargs["ExecutionParameters"][0] == "platform"
        assert call.kwargs["WorkGroup"] == "gateway-test-audit"

    @patch.dict("os.environ", _ENV, clear=False)
    @patch("budget_admin.audit_query._client")
    def test_admin_empty_result(self, mock_client: Any) -> None:
        mock_client.return_value = _mock_athena_client([])
        result = handler(self._audit_event("platform"))
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["count"] == 0
        assert body["items"] == []

    def test_non_admin_blocked_for_other_team_403(self) -> None:
        # A non-admin is denied at the top-level ADMIN_SCOPE gate (the route is
        # admin-only). The ADR-008 team-isolation guard below is defense-in-depth
        # for a future scope relaxation; see test_isolation_guard_* for it.
        result = handler(self._audit_event("platform", authorization=f"Bearer {_team_jwt('other-team')}"))
        assert result["statusCode"] == 403
        assert _err(result)["code"] == "forbidden"

    def test_isolation_guard_blocks_cross_team_read(self) -> None:
        # Directly exercise the ADR-008 guard in _get_audit: a non-admin reading
        # another team is a ForbiddenError even if the top gate were relaxed.
        from budget_admin.handler import _get_audit
        from gwcore import auth, errors

        principal = auth.Principal(sub="u", scopes=frozenset({auth.INVOKE_SCOPE}), team="my-team")
        with pytest.raises(errors.ForbiddenError) as exc_info:
            _get_audit(
                {"team": "other-team", "start": "2026-06-01T00:00:00Z", "end": "2026-06-30T00:00:00Z"}, principal
            )
        assert exc_info.value.status == 403

    def test_isolation_guard_empty_team_claim_cannot_bypass(self) -> None:
        # An empty team claim must NOT bypass the guard (would grant cross-team).
        from budget_admin.handler import _get_audit
        from gwcore import auth, errors

        principal = auth.Principal(sub="u", scopes=frozenset({auth.INVOKE_SCOPE}), team="")
        with pytest.raises(errors.ForbiddenError) as exc_info:
            _get_audit({"team": "platform", "start": "2026-06-01T00:00:00Z", "end": "2026-06-30T00:00:00Z"}, principal)
        assert exc_info.value.status == 403

    def test_missing_team_param_400(self) -> None:
        event = _make_event(
            method="GET",
            path="/audit",
            query_params={"start": "2026-06-01T00:00:00Z", "end": "2026-06-30T23:59:59Z"},
        )
        result = handler(event)
        assert result["statusCode"] == 400
        assert _err(result)["code"] == "validation_failed"

    def test_missing_start_end_400(self) -> None:
        result = handler(_make_event(method="GET", path="/audit", query_params={"team": "platform"}))
        assert result["statusCode"] == 400
        assert _err(result)["code"] == "validation_failed"

    @patch.dict("os.environ", _ENV, clear=False)
    def test_bad_iso_date_400(self) -> None:
        event = _make_event(
            method="GET",
            path="/audit",
            query_params={"team": "platform", "start": "not-a-date", "end": "2026-06-30T23:59:59Z"},
        )
        result = handler(event)
        assert result["statusCode"] == 400
        assert _err(result)["code"] == "validation_failed"

    def test_admin_unconfigured_workgroup_502(self) -> None:
        # An admin request with the audit surface not wired (no AUDIT_ATHENA_
        # WORKGROUP) surfaces a clean 502 "not configured", not a crash.
        saved = os.environ.pop("AUDIT_ATHENA_WORKGROUP", None)
        try:
            result = handler(self._audit_event("platform"))
            assert result["statusCode"] == 502
            assert _err(result)["code"] == "upstream_error"
        finally:
            if saved is not None:
                os.environ["AUDIT_ATHENA_WORKGROUP"] = saved


# ── Audit query unit tests (audit_query.py error/edge branches) ────────────────


class TestAuditQueryUnit:
    """Direct coverage of audit_query helpers: validation, polling, mapping."""

    def test_validate_iso8601_rejects_garbage(self) -> None:
        from budget_admin import audit_query
        from gwcore import errors

        with pytest.raises(errors.ValidationFailedError) as exc_info:
            audit_query._validate_iso8601("not-a-date", "start")
        assert exc_info.value.status == 400
        assert exc_info.value.to_body()["error"]["details"]["start"] == "not-a-date"

    def test_validate_iso8601_normalizes_trailing_z(self) -> None:
        from budget_admin import audit_query

        assert audit_query._validate_iso8601("2026-06-01T00:00:00Z", "start") == "2026-06-01T00:00:00+00:00"

    def test_coerce_limit_fallbacks_and_clamp(self) -> None:
        from budget_admin import audit_query

        assert audit_query._coerce_limit(None) == audit_query._DEFAULT_LIMIT
        assert audit_query._coerce_limit("") == audit_query._DEFAULT_LIMIT
        assert audit_query._coerce_limit("not-int") == audit_query._DEFAULT_LIMIT  # ValueError branch
        assert audit_query._coerce_limit(0) == audit_query._DEFAULT_LIMIT  # < 1 branch
        assert audit_query._coerce_limit(10_000) == audit_query._MAX_LIMIT  # clamp
        assert audit_query._coerce_limit(50) == 50

    def test_run_audit_query_workgroup_unset_raises_upstream(self) -> None:
        from budget_admin import audit_query
        from gwcore import errors

        saved = os.environ.pop("AUDIT_ATHENA_WORKGROUP", None)
        try:
            with (
                patch("budget_admin.audit_query._client", MagicMock()),
                pytest.raises(errors.UpstreamError) as exc_info,
            ):
                audit_query.run_audit_query(team="platform", start="2026-06-01T00:00:00Z", end="2026-06-30T00:00:00Z")
            assert exc_info.value.status == 502
        finally:
            if saved is not None:
                os.environ["AUDIT_ATHENA_WORKGROUP"] = saved

    @patch.dict("os.environ", {"AUDIT_ATHENA_WORKGROUP": "wg"}, clear=False)
    @patch("budget_admin.audit_query._client")
    def test_await_completion_failed_state_raises(self, mock_client: Any) -> None:
        from budget_admin import audit_query
        from gwcore import errors

        client = MagicMock()
        client.start_query_execution.return_value = {"QueryExecutionId": "q1"}
        client.get_query_execution.return_value = {
            "QueryExecution": {"Status": {"State": "FAILED", "StateChangeReason": "syntax error"}}
        }
        mock_client.return_value = client
        with pytest.raises(errors.UpstreamError) as exc_info:
            audit_query.run_audit_query(team="t", start="2026-06-01T00:00:00Z", end="2026-06-30T00:00:00Z")
        assert exc_info.value.to_body()["error"]["details"]["state"] == "FAILED"

    @patch.dict("os.environ", {"AUDIT_ATHENA_WORKGROUP": "wg"}, clear=False)
    @patch("budget_admin.audit_query._POLL_INTERVAL_SECONDS", 0)
    @patch("budget_admin.audit_query._MAX_POLLS", 2)
    @patch("budget_admin.audit_query._client")
    def test_await_completion_times_out(self, mock_client: Any) -> None:
        from budget_admin import audit_query
        from gwcore import errors

        client = MagicMock()
        client.start_query_execution.return_value = {"QueryExecutionId": "q1"}
        client.get_query_execution.return_value = {"QueryExecution": {"Status": {"State": "RUNNING"}}}
        mock_client.return_value = client
        with pytest.raises(errors.UpstreamError) as exc_info:
            audit_query.run_audit_query(team="t", start="2026-06-01T00:00:00Z", end="2026-06-30T00:00:00Z")
        assert "timed out" in exc_info.value.to_body()["error"]["message"].lower()

    def test_rows_to_records_empty(self) -> None:
        from budget_admin import audit_query

        assert audit_query._rows_to_records({}) == []
        assert audit_query._rows_to_records({"Rows": []}) == []

    def test_normalize_record_bad_status_coerces_to_none(self) -> None:
        from budget_admin import audit_query

        rec = audit_query._normalize_record({"status": "not-an-int", "action": "x"})
        assert rec["status"] is None  # int() ValueError branch → None
        assert rec["action"] == "x"

    def test_client_is_lazily_created_and_cached(self) -> None:
        from budget_admin import audit_query

        audit_query._athena_client = None
        with patch("budget_admin.audit_query.boto3.client", return_value=MagicMock()) as mock_boto:
            first = audit_query._client()
            second = audit_query._client()
        assert first is second
        assert mock_boto.call_count == 1  # cached after first create
        audit_query._athena_client = None  # reset module state for other tests


# ── Model validation tests (unchanged) ────────────────────────────────────────


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
            tier="unlimited",
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
