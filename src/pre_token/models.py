"""Pydantic v2 models for the Cognito Pre-Token-Generation V2 trigger event."""

from __future__ import annotations

from pydantic import BaseModel, Field

# -----------------------------------------------------------------------------
# Group Mapping Configuration
# -----------------------------------------------------------------------------


class GroupClaims(BaseModel):
    """Claims to inject into the token for a matched IdP group."""

    team: str
    org_unit: str
    cost_center: str
    tenant_tier: str


# -----------------------------------------------------------------------------
# Cognito Pre-Token-Generation V2 Event Models
# See: https://docs.aws.amazon.com/cognito/latest/developerguide/
#      user-pool-lambda-pre-token-generation.html
# -----------------------------------------------------------------------------


class GroupConfiguration(BaseModel):
    """Group configuration from the Cognito trigger event."""

    groups_to_override: list[str] | None = Field(default=None, alias="groupsToOverride")
    iam_roles_to_override: list[str] | None = Field(default=None, alias="iamRolesToOverride")
    preferred_role: str | None = Field(default=None, alias="preferredRole")

    model_config = {"populate_by_name": True}


class CallerContext(BaseModel):
    """Caller context from the Cognito trigger event."""

    aws_sdk_version: str = Field(default="", alias="awsSdkVersion")
    client_id: str = Field(default="", alias="clientId")

    model_config = {"populate_by_name": True}


class UserAttributes(BaseModel):
    """User attributes from the Cognito trigger event."""

    sub: str = ""
    email: str = ""
    email_verified: str = ""
    name: str = ""

    model_config = {"extra": "allow"}


class RequestContext(BaseModel):
    """Request portion of the Cognito trigger event."""

    user_attributes: UserAttributes = Field(default_factory=UserAttributes, alias="userAttributes")
    group_configuration: GroupConfiguration = Field(default_factory=GroupConfiguration, alias="groupConfiguration")
    scopes: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class ClaimsAndScopeOverrides(BaseModel):
    """Overrides for claims and scopes in the V2 response."""

    id_token_generation: dict[str, list[dict[str, str]]] = Field(default_factory=dict, alias="idTokenGeneration")
    access_token_generation: dict[str, list[dict[str, str]]] = Field(
        default_factory=dict, alias="accessTokenGeneration"
    )

    model_config = {"populate_by_name": True}


class ResponseContext(BaseModel):
    """Response portion of the Cognito trigger event."""

    claims_and_scope_overrides: ClaimsAndScopeOverrides = Field(
        default_factory=ClaimsAndScopeOverrides, alias="claimsAndScopeOverrides"
    )

    model_config = {"populate_by_name": True}


class PreTokenGenerationEvent(BaseModel):
    """Full Cognito Pre-Token-Generation V2 trigger event."""

    version: str = ""
    trigger_source: str = Field(default="", alias="triggerSource")
    region: str = Field(default="", alias="region")
    user_pool_id: str = Field(default="", alias="userPoolId")
    user_name: str = Field(default="", alias="userName")
    caller_context: CallerContext = Field(default_factory=CallerContext, alias="callerContext")
    request: RequestContext = Field(default_factory=RequestContext)
    response: ResponseContext = Field(default_factory=ResponseContext)

    model_config = {"populate_by_name": True}
