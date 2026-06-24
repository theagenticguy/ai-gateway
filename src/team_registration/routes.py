"""Route implementations for the team registration API (migrated onto gwcore).

Each function encapsulates the Cognito / DynamoDB interaction for one route.
Responses use the gwcore envelope; failures raise typed ``gwcore.errors``
(mapped to HTTP by the handler); the mutating routes (register / rotate /
deactivate) emit a ``gwcore.audit`` event since each creates or destroys a
Cognito app client.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import boto3
from botocore.exceptions import ClientError
from pydantic import ValidationError

from gwcore import audit, auth, errors, ok
from gwcore.responses import request_body
from team_registration.models import (
    TIER_BUDGET_DEFAULTS,
    CredentialsResponse,
    DeactivateResponse,
    RegisterTeamRequest,
    TeamListResponse,
    TeamResponse,
    TeamStatus,
    UsageSummary,
)

logger = logging.getLogger("team_registration.routes")

# ── AWS clients / config ─────────────────────────────────────────────────────

_region = os.environ.get("AWS_REGION", "us-east-1")

cognito = boto3.client("cognito-idp", region_name=_region)
dynamodb = boto3.resource("dynamodb", region_name=_region)

USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
TEAMS_TABLE = os.environ.get("TEAMS_TABLE", "gateway-teams")
BUDGETS_TABLE = os.environ.get("BUDGETS_TABLE", "gateway-budgets")
USAGE_TABLE = os.environ.get("USAGE_TABLE", "gateway-usage")
PROJECT_NAME = os.environ.get("PROJECT_NAME", "ai-gateway")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")

RESOURCE_SERVER_IDENTIFIER = os.environ.get("RESOURCE_SERVER_IDENTIFIER", "https://gateway.internal")
TOKEN_ENDPOINT = os.environ.get("TOKEN_ENDPOINT", "")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _current_period() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m")


def _client_error_code(e: ClientError) -> str:
    return str(e.response.get("Error", {}).get("Code", ""))


def _client_error_message(e: ClientError) -> str:
    return str(e.response.get("Error", {}).get("Message", "Unknown error"))


def _audit(event: dict[str, Any], principal: auth.Principal, **kw: Any) -> None:
    """Emit a control-plane audit event for a team mutation."""
    audit.emit(audit.event_from_request(event, actor=principal.sub, team=principal.team, **kw))


def _team_exists_by_name(team_name: str) -> bool:
    """Check if a team with the given name already exists (via GSI)."""
    table = dynamodb.Table(TEAMS_TABLE)
    resp = table.query(
        IndexName="team-name-index",
        KeyConditionExpression="team_name = :tn",
        ExpressionAttributeValues={":tn": team_name},
        Limit=1,
    )
    return len(resp.get("Items", [])) > 0


def _create_invoke_client(client_name: str) -> dict[str, Any]:
    """Create a Cognito app client with the client_credentials/invoke grant."""
    resp = cognito.create_user_pool_client(
        UserPoolId=USER_POOL_ID,
        ClientName=client_name,
        GenerateSecret=True,
        AllowedOAuthFlowsUserPoolClient=True,
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=[f"{RESOURCE_SERVER_IDENTIFIER}/invoke"],
        AccessTokenValidity=1,
        TokenValidityUnits={"AccessToken": "hours"},
    )
    return resp["UserPoolClient"]


# ── POST /teams ──────────────────────────────────────────────────────────────


def register_team(event: dict[str, Any], principal: auth.Principal) -> dict[str, Any]:
    """Register a new team: create Cognito client, store metadata, seed budget."""
    try:
        request = RegisterTeamRequest.model_validate_json(request_body(event))
    except ValidationError as e:
        raise errors.ValidationFailedError("Invalid team registration", details={"errors": e.errors()}) from e

    if _team_exists_by_name(request.team_name):
        raise errors.ConflictError(f"Team '{request.team_name}' already exists")

    team_id = str(uuid.uuid4())
    now = _now_iso()
    client_name = f"{PROJECT_NAME}-{request.team_name}-{ENVIRONMENT}"

    try:
        client_data = _create_invoke_client(client_name)
    except ClientError as e:
        logger.exception("Failed to create Cognito client for team=%s", request.team_name)
        raise errors.UpstreamError("Cognito error", details={"message": _client_error_message(e)}) from e

    client_id = client_data["ClientId"]
    client_secret = client_data.get("ClientSecret", "")

    dynamodb.Table(TEAMS_TABLE).put_item(
        Item={
            "team_id": team_id,
            "team_name": request.team_name,
            "contact_email": request.contact_email,
            "tier": request.tier.value,
            "description": request.description,
            "status": TeamStatus.ACTIVE,
            "client_id": client_id,
            "cognito_client_name": client_name,
            "created_at": now,
            "updated_at": now,
        }
    )

    budget = TIER_BUDGET_DEFAULTS.get(request.tier.value, 1000)
    dynamodb.Table(BUDGETS_TABLE).put_item(
        Item={
            "pk": f"BUDGET#{request.team_name}",
            "sk": "CONFIG",
            "team_id": team_id,
            "team_name": request.team_name,
            "monthly_budget_usd": Decimal(str(budget)),
            "warn_threshold_pct": 80,
            "hard_limit_pct": 100,
            "tier": request.tier.value,
            "created_at": now,
        }
    )

    logger.info("Registered team=%s id=%s tier=%s", request.team_name, team_id, request.tier.value)
    _audit(
        event,
        principal,
        action="team.create",
        resource=team_id,
        after={"team_name": request.team_name, "tier": request.tier.value, "client_id": client_id},
        status=201,
    )

    credentials = CredentialsResponse(client_id=client_id, client_secret=client_secret, token_endpoint=TOKEN_ENDPOINT)
    return ok(
        {
            "team_id": team_id,
            "team_name": request.team_name,
            "tier": request.tier.value,
            "credentials": credentials.model_dump(),
            "setup_instructions": {
                "step_1": f"Export CLIENT_ID={client_id} and CLIENT_SECRET=<secret>",
                "step_2": (
                    f"POST {TOKEN_ENDPOINT} with grant_type=client_credentials, "
                    f"scope={RESOURCE_SERVER_IDENTIFIER}/invoke"
                ),
                "step_3": "Use the access_token in the Authorization: Bearer header for gateway requests.",
            },
        },
        status=201,
    )


# ── GET /teams ───────────────────────────────────────────────────────────────


def list_teams() -> dict[str, Any]:
    """Return all active teams."""
    resp = dynamodb.Table(TEAMS_TABLE).scan(
        FilterExpression="attribute_not_exists(#s) OR #s = :active",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":active": TeamStatus.ACTIVE},
    )
    teams = [
        TeamResponse(
            team_id=item["team_id"],
            team_name=item["team_name"],
            client_id=item.get("client_id", ""),
            tier=item.get("tier", "standard"),
            status=item.get("status", TeamStatus.ACTIVE),
            description=item.get("description", ""),
            contact_email=item.get("contact_email", ""),
            created_at=item.get("created_at", ""),
            updated_at=item.get("updated_at", ""),
        )
        for item in resp.get("Items", [])
    ]
    return ok(TeamListResponse(teams=teams, count=len(teams)).model_dump())


# ── GET /teams/{id} ──────────────────────────────────────────────────────────


def get_team(team_id: str) -> dict[str, Any]:
    """Get team details, current usage, and budget."""
    resp = dynamodb.Table(TEAMS_TABLE).get_item(Key={"team_id": team_id})
    item = resp.get("Item")
    if not item:
        raise errors.NotFoundError(f"Team {team_id} not found")

    usage_summary = _get_usage_summary(item.get("team_name", ""), item.get("tier", "standard"))
    team = TeamResponse(
        team_id=item["team_id"],
        team_name=item["team_name"],
        client_id=item.get("client_id", ""),
        tier=item.get("tier", "standard"),
        status=item.get("status", TeamStatus.ACTIVE),
        description=item.get("description", ""),
        contact_email=item.get("contact_email", ""),
        created_at=item.get("created_at", ""),
        updated_at=item.get("updated_at", ""),
        usage_summary=usage_summary,
    )
    return ok(team.model_dump())


def _get_usage_summary(team_name: str, tier: str) -> UsageSummary:
    """Build a usage summary from the budgets and usage tables."""
    period = _current_period()
    budget = float(TIER_BUDGET_DEFAULTS.get(tier, 1000))

    try:
        budget_resp = dynamodb.Table(BUDGETS_TABLE).get_item(Key={"pk": f"BUDGET#{team_name}", "sk": "CONFIG"})
        budget_item = budget_resp.get("Item")
        if budget_item:
            budget = float(Decimal(str(budget_item.get("monthly_budget_usd", budget))))
    except (ClientError, InvalidOperation):
        logger.debug("Could not fetch budget for team=%s, using tier default", team_name)

    total_cost = 0.0
    try:
        usage_resp = dynamodb.Table(USAGE_TABLE).get_item(
            Key={"pk": f"USAGE#TEAM#{team_name}", "sk": f"PERIOD#{period}"}
        )
        usage_item = usage_resp.get("Item")
        if usage_item:
            total_cost = float(Decimal(str(usage_item.get("total_cost_usd", "0"))))
    except (ClientError, InvalidOperation):
        logger.debug("Could not fetch usage for team=%s", team_name)

    utilization = (total_cost / budget * 100) if budget > 0 else 0.0
    return UsageSummary(
        period=period,
        total_cost_usd=total_cost,
        monthly_budget_usd=budget,
        utilization_pct=round(utilization, 2),
    )


# ── POST /teams/{id}/rotate ─────────────────────────────────────────────────


def rotate_credentials(team_id: str, event: dict[str, Any], principal: auth.Principal) -> dict[str, Any]:
    """Rotate a team's Cognito client credentials (delete old client, create new)."""
    table = dynamodb.Table(TEAMS_TABLE)
    item = table.get_item(Key={"team_id": team_id}).get("Item")
    if not item:
        raise errors.NotFoundError(f"Team {team_id} not found")
    if item.get("status") == TeamStatus.INACTIVE:
        raise errors.ValidationFailedError("Cannot rotate credentials for an inactive team")

    old_client_id = item.get("client_id", "")
    team_name = item["team_name"]

    if old_client_id:
        try:
            cognito.delete_user_pool_client(UserPoolId=USER_POOL_ID, ClientId=old_client_id)
        except ClientError as e:
            if _client_error_code(e) != "ResourceNotFoundException":
                logger.exception("Failed to delete old Cognito client for team=%s", team_name)
                raise errors.UpstreamError(
                    "Failed to delete old client", details={"message": _client_error_message(e)}
                ) from e

    client_name = item.get("cognito_client_name", f"{PROJECT_NAME}-{team_name}-{ENVIRONMENT}")
    try:
        new_client = _create_invoke_client(client_name)
    except ClientError as e:
        logger.exception("Failed to create new Cognito client for team=%s", team_name)
        raise errors.UpstreamError("Cognito error", details={"message": _client_error_message(e)}) from e

    new_client_id = new_client["ClientId"]
    new_client_secret = new_client.get("ClientSecret", "")

    table.update_item(
        Key={"team_id": team_id},
        UpdateExpression="SET client_id = :cid, updated_at = :now",
        ExpressionAttributeValues={":cid": new_client_id, ":now": _now_iso()},
    )

    logger.info("Client rotation complete for team=%s", team_name)  # nosemgrep: python-logger-credential-disclosure
    _audit(
        event,
        principal,
        action="team.rotate",
        resource=team_id,
        detail=f"rotated client_id {old_client_id} -> {new_client_id}",
    )

    credentials = CredentialsResponse(
        client_id=new_client_id, client_secret=new_client_secret, token_endpoint=TOKEN_ENDPOINT
    )
    return ok(credentials.model_dump())


# ── DELETE /teams/{id} ───────────────────────────────────────────────────────


def deactivate_team(team_id: str, event: dict[str, Any], principal: auth.Principal) -> dict[str, Any]:
    """Deactivate a team: delete its Cognito client (revokes all tokens) and mark inactive."""
    table = dynamodb.Table(TEAMS_TABLE)
    item = table.get_item(Key={"team_id": team_id}).get("Item")
    if not item:
        raise errors.NotFoundError(f"Team {team_id} not found")
    if item.get("status") == TeamStatus.INACTIVE:
        raise errors.ValidationFailedError("Team is already inactive")

    client_id = item.get("client_id", "")
    team_name = item["team_name"]

    if client_id:
        try:
            cognito.delete_user_pool_client(UserPoolId=USER_POOL_ID, ClientId=client_id)
        except ClientError as e:
            if _client_error_code(e) != "ResourceNotFoundException":
                logger.exception("Failed to delete Cognito client for team=%s", team_name)
                raise errors.UpstreamError(
                    "Failed to delete Cognito client", details={"message": _client_error_message(e)}
                ) from e

    table.update_item(
        Key={"team_id": team_id},
        UpdateExpression="SET #s = :inactive, updated_at = :now, client_id = :empty",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":inactive": TeamStatus.INACTIVE, ":now": _now_iso(), ":empty": ""},
    )

    logger.info("Deactivated team=%s id=%s", team_name, team_id)
    _audit(event, principal, action="team.deactivate", resource=team_id, detail=f"team={team_name}")

    return ok(DeactivateResponse(team_id=team_id).model_dump())
