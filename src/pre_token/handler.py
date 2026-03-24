"""Lambda handler for Cognito Pre-Token-Generation V2 trigger.

Extracts IdP group memberships from the trigger event and maps them
to custom gateway claims (team, org_unit, cost_center, tenant_tier)
using a configurable GROUP_MAPPING environment variable.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from pydantic import ValidationError

from pre_token.models import GroupClaims, PreTokenGenerationEvent

logger = logging.getLogger("pre_token")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(
        logging.Formatter(
            json.dumps(
                {
                    "timestamp": "%(asctime)s",
                    "level": "%(levelname)s",
                    "logger": "%(name)s",
                    "message": "%(message)s",
                }
            )
        )
    )
    logger.addHandler(_h)


def _load_group_mapping() -> dict[str, GroupClaims]:
    """Load and validate the group mapping from the GROUP_MAPPING env var."""
    raw = os.environ.get("GROUP_MAPPING", "{}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("GROUP_MAPPING is not valid JSON, using empty mapping")
        return {}

    mapping: dict[str, GroupClaims] = {}
    for group_name, claims_data in parsed.items():
        try:
            mapping[group_name] = GroupClaims.model_validate(claims_data)
        except ValidationError:
            logger.warning("Invalid claims for group '%s', skipping", group_name)
    return mapping


def _resolve_claims(groups: list[str], mapping: dict[str, GroupClaims]) -> GroupClaims | None:
    """Find the first matching group in the mapping and return its claims.

    Priority is determined by the order groups appear in the user's group list.
    """
    for group in groups:
        if group in mapping:
            return mapping[group]
    return None


def _build_claim_overrides(claims: GroupClaims) -> list[dict[str, str]]:
    """Build the claimsToAddOrOverride list for the V2 response."""
    return [
        {"claimKey": "custom:team", "claimValue": claims.team},
        {"claimKey": "custom:org_unit", "claimValue": claims.org_unit},
        {"claimKey": "custom:cost_center", "claimValue": claims.cost_center},
        {"claimKey": "custom:tenant_tier", "claimValue": claims.tenant_tier},
    ]


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Cognito Pre-Token-Generation V2 Lambda handler.

    Reads group memberships from the trigger event, maps them to gateway
    claims via GROUP_MAPPING, and injects the claims into both the ID token
    and access token.
    """
    try:
        trigger = PreTokenGenerationEvent.model_validate(event)
    except ValidationError:
        logger.exception("Failed to validate trigger event")
        return event

    # Extract user groups from the groupConfiguration or SAML/OIDC assertions
    groups: list[str] = []

    # V2 trigger: groups come from groupConfiguration.groupsToOverride
    group_config = trigger.request.group_configuration
    if group_config.groups_to_override:
        groups = group_config.groups_to_override

    # Also check for cognito:groups in user attributes (SAML mapped)
    user_attrs = trigger.request.user_attributes
    if hasattr(user_attrs, "cognito_groups") and not groups:
        cognito_groups = getattr(user_attrs, "cognito_groups", "")
        if cognito_groups:
            groups = [g.strip() for g in cognito_groups.split(",") if g.strip()]

    # nosemgrep: python.lang.security.audit.logging.logger-credential-leak  # noqa: ERA001
    logger.info(
        "Processing pre-token for user '%s', group_count=%d",
        trigger.user_name,
        len(groups),
    )

    mapping = _load_group_mapping()
    if not mapping:
        logger.info("No group mapping configured, returning event unchanged")
        return event

    claims = _resolve_claims(groups, mapping)
    if claims is None:
        logger.info("No matching group found for user '%s'", trigger.user_name)
        return event

    logger.info(
        "Mapped user '%s' to team='%s', tier='%s'",
        trigger.user_name,
        claims.team,
        claims.tenant_tier,
    )

    claim_overrides = _build_claim_overrides(claims)

    # Inject into both ID token and access token
    event.setdefault("response", {})
    event["response"].setdefault("claimsAndScopeOverrides", {})

    overrides = event["response"]["claimsAndScopeOverrides"]

    overrides["idTokenGeneration"] = {"claimsToAddOrOverride": claim_overrides}
    overrides["accessTokenGeneration"] = {"claimsToAddOrOverride": claim_overrides}

    return event
