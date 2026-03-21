"""Tests for the team registration self-service API.

Covers registration (happy path, duplicate name, invalid tier),
credential rotation, deactivation, list/get, and auth validation.
All Cognito IDP and DynamoDB interactions are mocked.
"""

from __future__ import annotations

import base64
import json
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from team_registration.auth import (
    REQUIRED_SCOPE,
    decode_jwt_claims,
    extract_bearer_token,
    validate_admin_scope,
)
from team_registration.handler import handler
from team_registration.models import (
    TIER_BUDGET_DEFAULTS,
    CredentialsResponse,
    DeactivateResponse,
    RegisterTeamRequest,
    TeamListResponse,
    TeamResponse,
    TeamStatus,
    Tier,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_jwt(claims: dict[str, Any]) -> str:
    """Build a fake JWT with the given payload claims."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(b"fakesig").decode().rstrip("=")
    return f"{header}.{payload}.{signature}"


ADMIN_JWT = _make_jwt({"scope": REQUIRED_SCOPE, "sub": "admin-user"})
NON_ADMIN_JWT = _make_jwt({"scope": "https://gateway.internal/invoke", "sub": "regular-user"})

SAMPLE_TEAM_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _make_event(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    token: str = ADMIN_JWT,
) -> dict[str, Any]:
    """Build a Lambda Function URL event."""
    event: dict[str, Any] = {
        "requestContext": {"http": {"method": method, "path": path}},
        "headers": {"authorization": f"Bearer {token}"},
        "isBase64Encoded": False,
    }
    if body is not None:
        event["body"] = json.dumps(body)
    else:
        event["body"] = ""
    return event


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required env vars for the Lambda."""
    monkeypatch.setenv("USER_POOL_ID", "us-east-1_TestPool")
    monkeypatch.setenv("TEAMS_TABLE", "gateway-teams")
    monkeypatch.setenv("BUDGETS_TABLE", "gateway-budgets")
    monkeypatch.setenv("USAGE_TABLE", "gateway-usage")
    monkeypatch.setenv("TOKEN_ENDPOINT", "https://test.auth.us-east-1.amazoncognito.com/oauth2/token")
    monkeypatch.setenv("RESOURCE_SERVER_IDENTIFIER", "https://gateway.internal")


def _make_cognito_response(client_id: str = "new-client-id", client_secret: str = "new-secret") -> dict[str, Any]:  # noqa: S107
    return {
        "UserPoolClient": {
            "ClientId": client_id,
            "ClientSecret": client_secret,
            "ClientName": "ai-gateway-test-team-dev",
        }
    }


def _make_team_item(
    team_id: str = SAMPLE_TEAM_ID,
    team_name: str = "test-team",
    status: str = TeamStatus.ACTIVE,
    client_id: str = "existing-client-id",
    tier: str = "standard",
) -> dict[str, Any]:
    return {
        "team_id": team_id,
        "team_name": team_name,
        "contact_email": "team@example.com",
        "tier": tier,
        "description": "A test team",
        "status": status,
        "client_id": client_id,
        "cognito_client_name": "ai-gateway-test-team-dev",
        "created_at": "2026-03-21T00:00:00+00:00",
        "updated_at": "2026-03-21T00:00:00+00:00",
    }


# ── Auth tests ───────────────────────────────────────────────────────────────


class TestAuth:
    def test_extract_bearer_token_valid(self) -> None:
        event = {"headers": {"authorization": "Bearer abc123"}}
        assert extract_bearer_token(event) == "abc123"

    def test_extract_bearer_token_missing(self) -> None:
        event = {"headers": {}}
        assert extract_bearer_token(event) is None

    def test_extract_bearer_token_no_bearer_prefix(self) -> None:
        event = {"headers": {"authorization": "Basic abc123"}}
        assert extract_bearer_token(event) is None

    def test_decode_jwt_claims_valid(self) -> None:
        token = _make_jwt({"sub": "test", "scope": "admin"})
        claims = decode_jwt_claims(token)
        assert claims["sub"] == "test"

    def test_decode_jwt_claims_invalid(self) -> None:
        assert decode_jwt_claims("garbage") == {}

    def test_validate_admin_scope_success(self) -> None:
        event = _make_event("GET", "/teams")
        assert validate_admin_scope(event) is None

    def test_validate_admin_scope_missing_header(self) -> None:
        event = {"headers": {}, "requestContext": {"http": {"method": "GET", "path": "/teams"}}}
        result = validate_admin_scope(event)
        assert result is not None
        assert "Missing" in result

    def test_validate_admin_scope_wrong_scope(self) -> None:
        event = _make_event("GET", "/teams", token=NON_ADMIN_JWT)
        result = validate_admin_scope(event)
        assert result is not None
        assert "Missing required scope" in result

    def test_handler_returns_403_for_non_admin(self) -> None:
        event = _make_event("GET", "/teams", token=NON_ADMIN_JWT)
        result = handler(event)
        assert result["statusCode"] == 403


# ── Registration tests ───────────────────────────────────────────────────────


class TestRegisterTeam:
    @patch("team_registration.routes.dynamodb")
    @patch("team_registration.routes.cognito")
    def test_happy_path(self, mock_cognito: MagicMock, mock_dynamodb: MagicMock) -> None:
        mock_cognito.create_user_pool_client.return_value = _make_cognito_response()
        mock_table = MagicMock()
        mock_table.query.return_value = {"Items": []}
        mock_dynamodb.Table.return_value = mock_table

        event = _make_event(
            "POST",
            "/teams",
            body={
                "team_name": "new-team",
                "contact_email": "lead@example.com",
                "tier": "standard",
                "description": "Our new team",
            },
        )
        result = handler(event)
        body = json.loads(result["body"])

        assert result["statusCode"] == 201
        assert body["team_name"] == "new-team"
        assert body["credentials"]["client_id"] == "new-client-id"
        assert body["credentials"]["client_secret"] == "new-secret"  # noqa: S105
        assert "setup_instructions" in body

        # Verify Cognito was called
        mock_cognito.create_user_pool_client.assert_called_once()

    @patch("team_registration.routes.dynamodb")
    @patch("team_registration.routes.cognito")
    def test_duplicate_name(self, mock_cognito: MagicMock, mock_dynamodb: MagicMock) -> None:
        mock_table = MagicMock()
        mock_table.query.return_value = {"Items": [{"team_id": "existing"}]}
        mock_dynamodb.Table.return_value = mock_table

        event = _make_event(
            "POST",
            "/teams",
            body={
                "team_name": "existing-team",
                "contact_email": "team@example.com",
            },
        )
        result = handler(event)
        body = json.loads(result["body"])

        assert result["statusCode"] == 409
        assert "already exists" in body["error"]

    def test_invalid_tier(self) -> None:
        event = _make_event(
            "POST",
            "/teams",
            body={
                "team_name": "new-team",
                "contact_email": "team@example.com",
                "tier": "ultra-mega",
            },
        )
        result = handler(event)
        assert result["statusCode"] == 400

    def test_invalid_team_name_format(self) -> None:
        event = _make_event(
            "POST",
            "/teams",
            body={
                "team_name": "bad name with spaces!",
                "contact_email": "team@example.com",
            },
        )
        result = handler(event)
        assert result["statusCode"] == 400

    def test_missing_required_fields(self) -> None:
        event = _make_event("POST", "/teams", body={"description": "no name"})
        result = handler(event)
        assert result["statusCode"] == 400

    def test_invalid_email(self) -> None:
        event = _make_event(
            "POST",
            "/teams",
            body={
                "team_name": "new-team",
                "contact_email": "not-an-email",
            },
        )
        result = handler(event)
        assert result["statusCode"] == 400

    @patch("team_registration.routes.dynamodb")
    @patch("team_registration.routes.cognito")
    def test_cognito_error(self, mock_cognito: MagicMock, mock_dynamodb: MagicMock) -> None:
        mock_table = MagicMock()
        mock_table.query.return_value = {"Items": []}
        mock_dynamodb.Table.return_value = mock_table
        mock_cognito.create_user_pool_client.side_effect = ClientError(
            {"Error": {"Code": "LimitExceededException", "Message": "Too many clients"}},
            "CreateUserPoolClient",
        )

        event = _make_event(
            "POST",
            "/teams",
            body={
                "team_name": "new-team",
                "contact_email": "team@example.com",
            },
        )
        result = handler(event)
        body = json.loads(result["body"])

        assert result["statusCode"] == 500
        assert "Cognito error" in body["error"]

    @patch("team_registration.routes.dynamodb")
    @patch("team_registration.routes.cognito")
    def test_free_tier_budget(self, mock_cognito: MagicMock, mock_dynamodb: MagicMock) -> None:
        """Free tier registration should seed a $10 budget."""
        mock_cognito.create_user_pool_client.return_value = _make_cognito_response()
        mock_table = MagicMock()
        mock_table.query.return_value = {"Items": []}
        mock_dynamodb.Table.return_value = mock_table

        event = _make_event(
            "POST",
            "/teams",
            body={
                "team_name": "free-team",
                "contact_email": "free@example.com",
                "tier": "free",
            },
        )
        result = handler(event)

        assert result["statusCode"] == 201

        # Check the budget put_item call — second put_item is the budget record
        calls = mock_table.put_item.call_args_list
        assert len(calls) == 2
        budget_item = calls[1].kwargs["Item"]
        assert budget_item["monthly_budget_usd"] == Decimal(10)


# ── List teams tests ─────────────────────────────────────────────────────────


class TestListTeams:
    @patch("team_registration.routes.dynamodb")
    def test_list_active_teams(self, mock_dynamodb: MagicMock) -> None:
        mock_table = MagicMock()
        second_id = "22222222-3333-4444-5555-666666666666"
        mock_table.scan.return_value = {
            "Items": [_make_team_item(), _make_team_item(team_id=second_id, team_name="team-b")]
        }
        mock_dynamodb.Table.return_value = mock_table

        event = _make_event("GET", "/teams")
        result = handler(event)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert body["count"] == 2
        assert len(body["teams"]) == 2

    @patch("team_registration.routes.dynamodb")
    def test_list_empty(self, mock_dynamodb: MagicMock) -> None:
        mock_table = MagicMock()
        mock_table.scan.return_value = {"Items": []}
        mock_dynamodb.Table.return_value = mock_table

        event = _make_event("GET", "/teams")
        result = handler(event)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert body["count"] == 0


# ── Get team tests ───────────────────────────────────────────────────────────


class TestGetTeam:
    @patch("team_registration.routes.dynamodb")
    def test_get_existing_team(self, mock_dynamodb: MagicMock) -> None:
        mock_table = MagicMock()
        team_item = _make_team_item()
        mock_table.get_item.return_value = {"Item": team_item}
        mock_dynamodb.Table.return_value = mock_table

        event = _make_event("GET", f"/teams/{SAMPLE_TEAM_ID}")
        result = handler(event)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert body["team_id"] == SAMPLE_TEAM_ID
        assert body["team_name"] == "test-team"
        assert body["usage_summary"] is not None
        assert "period" in body["usage_summary"]

    @patch("team_registration.routes.dynamodb")
    def test_get_missing_team(self, mock_dynamodb: MagicMock) -> None:
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}
        mock_dynamodb.Table.return_value = mock_table

        event = _make_event("GET", f"/teams/{SAMPLE_TEAM_ID}")
        result = handler(event)

        assert result["statusCode"] == 404

    def test_get_invalid_id_format(self) -> None:
        event = _make_event("GET", "/teams/not-a-uuid")
        result = handler(event)
        assert result["statusCode"] == 404


# ── Credential rotation tests ────────────────────────────────────────────────


class TestRotateCredentials:
    @patch("team_registration.routes.dynamodb")
    @patch("team_registration.routes.cognito")
    def test_happy_path(self, mock_cognito: MagicMock, mock_dynamodb: MagicMock) -> None:
        mock_table = MagicMock()
        mock_table.get_item.return_value = {"Item": _make_team_item()}
        mock_dynamodb.Table.return_value = mock_table
        mock_cognito.create_user_pool_client.return_value = _make_cognito_response(
            client_id="rotated-client-id",
            client_secret="rotated-secret",  # noqa: S106
        )

        event = _make_event("POST", f"/teams/{SAMPLE_TEAM_ID}/rotate")
        result = handler(event)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert body["client_id"] == "rotated-client-id"
        assert body["client_secret"] == "rotated-secret"  # noqa: S105

        # Old client should have been deleted
        mock_cognito.delete_user_pool_client.assert_called_once()

        # DynamoDB should have been updated
        mock_table.update_item.assert_called_once()

    @patch("team_registration.routes.dynamodb")
    def test_team_not_found(self, mock_dynamodb: MagicMock) -> None:
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}
        mock_dynamodb.Table.return_value = mock_table

        event = _make_event("POST", f"/teams/{SAMPLE_TEAM_ID}/rotate")
        result = handler(event)

        assert result["statusCode"] == 404

    @patch("team_registration.routes.dynamodb")
    def test_inactive_team(self, mock_dynamodb: MagicMock) -> None:
        mock_table = MagicMock()
        mock_table.get_item.return_value = {"Item": _make_team_item(status=TeamStatus.INACTIVE)}
        mock_dynamodb.Table.return_value = mock_table

        event = _make_event("POST", f"/teams/{SAMPLE_TEAM_ID}/rotate")
        result = handler(event)

        assert result["statusCode"] == 400

    @patch("team_registration.routes.dynamodb")
    @patch("team_registration.routes.cognito")
    def test_old_client_already_deleted(self, mock_cognito: MagicMock, mock_dynamodb: MagicMock) -> None:
        """Rotation should succeed even if the old client was already deleted."""
        mock_table = MagicMock()
        mock_table.get_item.return_value = {"Item": _make_team_item()}
        mock_dynamodb.Table.return_value = mock_table
        mock_cognito.delete_user_pool_client.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Client not found"}},
            "DeleteUserPoolClient",
        )
        mock_cognito.create_user_pool_client.return_value = _make_cognito_response()

        event = _make_event("POST", f"/teams/{SAMPLE_TEAM_ID}/rotate")
        result = handler(event)

        assert result["statusCode"] == 200


# ── Deactivation tests ──────────────────────────────────────────────────────


class TestDeactivateTeam:
    @patch("team_registration.routes.dynamodb")
    @patch("team_registration.routes.cognito")
    def test_happy_path(self, mock_cognito: MagicMock, mock_dynamodb: MagicMock) -> None:
        mock_table = MagicMock()
        mock_table.get_item.return_value = {"Item": _make_team_item()}
        mock_dynamodb.Table.return_value = mock_table

        event = _make_event("DELETE", f"/teams/{SAMPLE_TEAM_ID}")
        result = handler(event)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert body["team_id"] == SAMPLE_TEAM_ID
        assert body["status"] == TeamStatus.INACTIVE
        mock_cognito.delete_user_pool_client.assert_called_once()
        mock_table.update_item.assert_called_once()

    @patch("team_registration.routes.dynamodb")
    def test_team_not_found(self, mock_dynamodb: MagicMock) -> None:
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}
        mock_dynamodb.Table.return_value = mock_table

        event = _make_event("DELETE", f"/teams/{SAMPLE_TEAM_ID}")
        result = handler(event)

        assert result["statusCode"] == 404

    @patch("team_registration.routes.dynamodb")
    def test_already_inactive(self, mock_dynamodb: MagicMock) -> None:
        mock_table = MagicMock()
        mock_table.get_item.return_value = {"Item": _make_team_item(status=TeamStatus.INACTIVE)}
        mock_dynamodb.Table.return_value = mock_table

        event = _make_event("DELETE", f"/teams/{SAMPLE_TEAM_ID}")
        result = handler(event)

        assert result["statusCode"] == 400

    @patch("team_registration.routes.dynamodb")
    @patch("team_registration.routes.cognito")
    def test_cognito_client_already_gone(self, mock_cognito: MagicMock, mock_dynamodb: MagicMock) -> None:
        """Deactivation should succeed even if the Cognito client was already deleted."""
        mock_table = MagicMock()
        mock_table.get_item.return_value = {"Item": _make_team_item()}
        mock_dynamodb.Table.return_value = mock_table
        mock_cognito.delete_user_pool_client.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Client not found"}},
            "DeleteUserPoolClient",
        )

        event = _make_event("DELETE", f"/teams/{SAMPLE_TEAM_ID}")
        result = handler(event)

        assert result["statusCode"] == 200


# ── Handler routing tests ────────────────────────────────────────────────────


class TestHandlerRouting:
    def test_unknown_route_returns_404(self) -> None:
        event = _make_event("PATCH", "/teams/unknown")
        result = handler(event)
        assert result["statusCode"] == 404

    def test_invalid_json_body(self) -> None:
        event = _make_event("POST", "/teams")
        event["body"] = "not json!!!"
        result = handler(event)
        assert result["statusCode"] == 400

    def test_empty_body_for_post(self) -> None:
        event = _make_event("POST", "/teams")
        event["body"] = ""
        result = handler(event)
        assert result["statusCode"] == 400

    def test_trailing_slash_normalization(self) -> None:
        """Trailing slashes on /teams/ should still route correctly."""
        event = _make_event("GET", "/teams/")
        with patch("team_registration.routes.dynamodb") as mock_dynamodb:
            mock_table = MagicMock()
            mock_table.scan.return_value = {"Items": []}
            mock_dynamodb.Table.return_value = mock_table

            result = handler(event)
            assert result["statusCode"] == 200


# ── Models tests ─────────────────────────────────────────────────────────────


class TestModels:
    def test_register_team_request_defaults(self) -> None:
        req = RegisterTeamRequest(team_name="my-team", contact_email="a@b.com")
        assert req.tier == Tier.STANDARD
        assert req.description == ""

    def test_register_team_request_validation(self) -> None:
        from pydantic import ValidationError  # noqa: PLC0415

        with pytest.raises(ValidationError):
            RegisterTeamRequest(team_name="", contact_email="a@b.com")

    def test_tier_budget_defaults(self) -> None:
        assert TIER_BUDGET_DEFAULTS[Tier.FREE] == 10
        assert TIER_BUDGET_DEFAULTS[Tier.STANDARD] == 1000
        assert TIER_BUDGET_DEFAULTS[Tier.PREMIUM] == 10000
        assert TIER_BUDGET_DEFAULTS[Tier.ENTERPRISE] == 100000

    def test_credentials_response(self) -> None:
        resp = CredentialsResponse(
            client_id="abc",
            client_secret="def",  # noqa: S106
            token_endpoint="https://example.com/oauth2/token",  # noqa: S106
        )
        dumped = resp.model_dump()
        assert dumped["client_id"] == "abc"
        assert "expire" in dumped["expires_note"].lower()

    def test_team_response_serialization(self) -> None:
        resp = TeamResponse(
            team_id="123",
            team_name="test",
            client_id="cid",
            tier="standard",
            status="active",
            created_at="2026-01-01",
        )
        dumped = resp.model_dump()
        assert dumped["team_id"] == "123"

    def test_deactivate_response(self) -> None:
        resp = DeactivateResponse(team_id="abc")
        assert resp.status == TeamStatus.INACTIVE
        assert "revoked" in resp.message.lower()

    def test_team_list_response(self) -> None:
        resp = TeamListResponse(teams=[], count=0)
        assert resp.count == 0
