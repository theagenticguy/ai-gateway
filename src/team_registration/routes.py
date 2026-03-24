"""Route implementations for the team registration API.

Each function corresponds to an API route and encapsulates all
Cognito / DynamoDB interaction for that operation.
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
    """Safely extract the error code from a ClientError."""
    return str(e.response.get("Error", {}).get("Code", ""))


def _client_error_message(e: ClientError) -> str:
    """Safely extract the error message from a ClientError."""
    return str(e.response.get("Error", {}).get("Message", "Unknown error"))


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


# ── POST /teams ──────────────────────────────────────────────────────────────


def register_team(body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Register a new team: create Cognito client, store metadata, seed budget."""
    request = RegisterTeamRequest.model_validate(body)

    # Duplicate check
    if _team_exists_by_name(request.team_name):
        return {"error": f"Team '{request.team_name}' already exists"}, 409

    team_id = str(uuid.uuid4())
    now = _now_iso()

    # 1. Create Cognito app client
    client_name = f"{PROJECT_NAME}-{request.team_name}-{ENVIRONMENT}"
    allowed_scopes = [f"{RESOURCE_SERVER_IDENTIFIER}/invoke"]

    try:
        cognito_resp = cognito.create_user_pool_client(
            UserPoolId=USER_POOL_ID,
            ClientName=client_name,
            GenerateSecret=True,
            AllowedOAuthFlowsUserPoolClient=True,
            AllowedOAuthFlows=["client_credentials"],
            AllowedOAuthScopes=allowed_scopes,
            AccessTokenValidity=1,
            TokenValidityUnits={"AccessToken": "hours"},
        )
    except ClientError as e:
        logger.exception("Failed to create Cognito client for team=%s", request.team_name)
        return {"error": f"Cognito error: {_client_error_message(e)}"}, 500

    client_data = cognito_resp["UserPoolClient"]
    client_id = client_data["ClientId"]
    client_secret = client_data.get("ClientSecret", "")

    # 2. Store team metadata in DynamoDB
    teams_table = dynamodb.Table(TEAMS_TABLE)
    teams_table.put_item(
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

    # 3. Create default budget record
    budget = TIER_BUDGET_DEFAULTS.get(request.tier.value, 1000)
    budgets_table = dynamodb.Table(BUDGETS_TABLE)
    budgets_table.put_item(
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

    credentials = CredentialsResponse(
        client_id=client_id,
        client_secret=client_secret,
        token_endpoint=TOKEN_ENDPOINT,
    )
    result = {
        "team_id": team_id,
        "team_name": request.team_name,
        "tier": request.tier.value,
        "credentials": credentials.model_dump(),
        "setup_instructions": {
            "step_1": f"Export CLIENT_ID={client_id} and CLIENT_SECRET=<secret>",
            "step_2": (
                f"POST {TOKEN_ENDPOINT} with grant_type=client_credentials, scope={RESOURCE_SERVER_IDENTIFIER}/invoke"
            ),
            "step_3": "Use the access_token in the Authorization: Bearer header for gateway requests.",
        },
    }
    return result, 201


# ── GET /teams ───────────────────────────────────────────────────────────────


def list_teams() -> tuple[dict[str, Any], int]:
    """Return all active teams."""
    table = dynamodb.Table(TEAMS_TABLE)
    resp = table.scan(
        FilterExpression="attribute_not_exists(#s) OR #s = :active",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":active": TeamStatus.ACTIVE},
    )
    items = resp.get("Items", [])

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
        for item in items
    ]

    return TeamListResponse(teams=teams, count=len(teams)).model_dump(), 200


# ── GET /teams/{id} ──────────────────────────────────────────────────────────


def get_team(team_id: str) -> tuple[dict[str, Any], int]:
    """Get team details, Cognito status, current usage, and budget."""
    table = dynamodb.Table(TEAMS_TABLE)
    resp = table.get_item(Key={"team_id": team_id})
    item = resp.get("Item")
    if not item:
        return {"error": f"Team {team_id} not found"}, 404

    # Fetch usage
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
    return team.model_dump(), 200


def _get_usage_summary(team_name: str, tier: str) -> UsageSummary:
    """Build a usage summary from the budgets and usage tables."""
    period = _current_period()
    budget = float(TIER_BUDGET_DEFAULTS.get(tier, 1000))

    # Try to get custom budget
    try:
        budgets_table = dynamodb.Table(BUDGETS_TABLE)
        budget_resp = budgets_table.get_item(Key={"pk": f"BUDGET#{team_name}", "sk": "CONFIG"})
        budget_item = budget_resp.get("Item")
        if budget_item:
            budget = float(Decimal(str(budget_item.get("monthly_budget_usd", budget))))
    except (ClientError, InvalidOperation):
        logger.debug("Could not fetch budget for team=%s, using tier default", team_name)

    # Get current spend
    total_cost = 0.0
    try:
        usage_table = dynamodb.Table(USAGE_TABLE)
        usage_resp = usage_table.get_item(Key={"pk": f"USAGE#TEAM#{team_name}", "sk": f"PERIOD#{period}"})
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


def rotate_credentials(team_id: str) -> tuple[dict[str, Any], int]:
    """Rotate a team's Cognito client credentials.

    1. Look up team record to get the old client_id.
    2. Delete the old Cognito user pool client.
    3. Create a new client with the same settings.
    4. Update DynamoDB with the new client_id.
    5. Return the new credentials.
    """
    table = dynamodb.Table(TEAMS_TABLE)
    resp = table.get_item(Key={"team_id": team_id})
    item = resp.get("Item")
    if not item:
        return {"error": f"Team {team_id} not found"}, 404

    if item.get("status") == TeamStatus.INACTIVE:
        return {"error": "Cannot rotate credentials for an inactive team"}, 400

    old_client_id = item.get("client_id", "")
    team_name = item["team_name"]

    # Delete old Cognito client
    if old_client_id:
        try:
            cognito.delete_user_pool_client(
                UserPoolId=USER_POOL_ID,
                ClientId=old_client_id,
            )
        except ClientError as e:
            # If already deleted, proceed
            if _client_error_code(e) != "ResourceNotFoundException":
                logger.exception("Failed to delete old Cognito client for team=%s", team_name)
                return {"error": f"Failed to delete old client: {_client_error_message(e)}"}, 500

    # Create new Cognito client
    client_name = item.get("cognito_client_name", f"{PROJECT_NAME}-{team_name}-{ENVIRONMENT}")
    allowed_scopes = [f"{RESOURCE_SERVER_IDENTIFIER}/invoke"]

    try:
        cognito_resp = cognito.create_user_pool_client(
            UserPoolId=USER_POOL_ID,
            ClientName=client_name,
            GenerateSecret=True,
            AllowedOAuthFlowsUserPoolClient=True,
            AllowedOAuthFlows=["client_credentials"],
            AllowedOAuthScopes=allowed_scopes,
            AccessTokenValidity=1,
            TokenValidityUnits={"AccessToken": "hours"},
        )
    except ClientError as e:
        logger.exception("Failed to create new Cognito client for team=%s", team_name)
        return {"error": f"Cognito error: {_client_error_message(e)}"}, 500

    new_client = cognito_resp["UserPoolClient"]
    new_client_id = new_client["ClientId"]
    new_client_secret = new_client.get("ClientSecret", "")

    # Update DynamoDB
    now = _now_iso()
    table.update_item(
        Key={"team_id": team_id},
        UpdateExpression="SET client_id = :cid, updated_at = :now",
        ExpressionAttributeValues={":cid": new_client_id, ":now": now},
    )

    logger.info("Rotated credentials for team=%s", team_name)

    credentials = CredentialsResponse(
        client_id=new_client_id,
        client_secret=new_client_secret,
        token_endpoint=TOKEN_ENDPOINT,
    )
    return credentials.model_dump(), 200


# ── DELETE /teams/{id} ───────────────────────────────────────────────────────


def deactivate_team(team_id: str) -> tuple[dict[str, Any], int]:
    """Deactivate a team: delete Cognito client and mark inactive.

    Deleting the Cognito client immediately invalidates all outstanding
    access tokens — there is no grace period.
    """
    table = dynamodb.Table(TEAMS_TABLE)
    resp = table.get_item(Key={"team_id": team_id})
    item = resp.get("Item")
    if not item:
        return {"error": f"Team {team_id} not found"}, 404

    if item.get("status") == TeamStatus.INACTIVE:
        return {"error": "Team is already inactive"}, 400

    client_id = item.get("client_id", "")
    team_name = item["team_name"]

    # Delete Cognito client
    if client_id:
        try:
            cognito.delete_user_pool_client(
                UserPoolId=USER_POOL_ID,
                ClientId=client_id,
            )
        except ClientError as e:
            if _client_error_code(e) != "ResourceNotFoundException":
                logger.exception("Failed to delete Cognito client for team=%s", team_name)
                return {"error": f"Failed to delete Cognito client: {_client_error_message(e)}"}, 500

    # Mark team as inactive
    now = _now_iso()
    table.update_item(
        Key={"team_id": team_id},
        UpdateExpression="SET #s = :inactive, updated_at = :now, client_id = :empty",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":inactive": TeamStatus.INACTIVE,
            ":now": now,
            ":empty": "",
        },
    )

    logger.info("Deactivated team=%s id=%s", team_name, team_id)

    result = DeactivateResponse(team_id=team_id)
    return result.model_dump(), 200
