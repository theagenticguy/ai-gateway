"""Unit tests for the Pre-Token-Generation V2 Lambda handler."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from pre_token.handler import _build_claim_overrides, _load_group_mapping, _resolve_claims, handler
from pre_token.models import GroupClaims, GroupConfiguration, PreTokenGenerationEvent

# ── GroupClaims model ────────────────────────────────────────────────────────


class TestGroupClaims:
    def test_valid_claims(self) -> None:
        claims = GroupClaims(team="platform", org_unit="ai-eng", cost_center="CC-1234", tenant_tier="admin")
        assert claims.team == "platform"
        assert claims.tenant_tier == "admin"

    def test_rejects_missing_fields(self) -> None:
        with pytest.raises(Exception):  # noqa: B017, PT011
            GroupClaims.model_validate({"team": "platform"})


# ── GroupConfiguration model ─────────────────────────────────────────────────


class TestGroupConfiguration:
    def test_parses_alias_fields(self) -> None:
        gc = GroupConfiguration.model_validate({"groupsToOverride": ["admin", "users"]})
        assert gc.groups_to_override == ["admin", "users"]

    def test_defaults_to_none(self) -> None:
        gc = GroupConfiguration.model_validate({})
        assert gc.groups_to_override is None


# ── PreTokenGenerationEvent model ────────────────────────────────────────────


class TestPreTokenGenerationEvent:
    def test_parses_minimal_event(self) -> None:
        event = PreTokenGenerationEvent.model_validate(
            {
                "version": "2",
                "triggerSource": "TokenGeneration_HostedAuth",
                "userName": "testuser",
                "request": {
                    "userAttributes": {"sub": "abc-123", "email": "test@example.com"},
                    "groupConfiguration": {"groupsToOverride": ["admins"]},
                },
                "response": {},
            }
        )
        assert event.user_name == "testuser"
        assert event.request.group_configuration.groups_to_override == ["admins"]

    def test_defaults_for_empty_event(self) -> None:
        event = PreTokenGenerationEvent.model_validate({})
        assert event.user_name == ""
        assert event.request.group_configuration.groups_to_override is None


# ── _load_group_mapping ──────────────────────────────────────────────────────


_VALID_MAPPING = json.dumps(
    {
        "admins": {"team": "platform", "org_unit": "ai-eng", "cost_center": "CC-1", "tenant_tier": "admin"},
    }
)


class TestLoadGroupMapping:
    @patch.dict("os.environ", {"GROUP_MAPPING": _VALID_MAPPING})
    def test_loads_valid_mapping(self) -> None:
        mapping = _load_group_mapping()
        assert "admins" in mapping
        assert mapping["admins"].team == "platform"

    @patch.dict("os.environ", {"GROUP_MAPPING": "not-json"})
    def test_returns_empty_on_invalid_json(self) -> None:
        mapping = _load_group_mapping()
        assert mapping == {}

    @patch.dict("os.environ", {"GROUP_MAPPING": json.dumps({"bad": {"team": "x"}})})
    def test_skips_invalid_entries(self) -> None:
        mapping = _load_group_mapping()
        assert "bad" not in mapping

    @patch.dict("os.environ", {}, clear=True)
    def test_returns_empty_when_unset(self) -> None:
        mapping = _load_group_mapping()
        assert mapping == {}


# ── _resolve_claims ──────────────────────────────────────────────────────────


class TestResolveClaims:
    def test_returns_first_matching_group(self) -> None:
        mapping = {
            "admins": GroupClaims(team="platform", org_unit="ai-eng", cost_center="CC-1", tenant_tier="admin"),
            "users": GroupClaims(team="general", org_unit="ai-eng", cost_center="CC-2", tenant_tier="standard"),
        }
        result = _resolve_claims(["users", "admins"], mapping)
        assert result is not None
        assert result.team == "general"

    def test_returns_none_when_no_match(self) -> None:
        mapping = {
            "admins": GroupClaims(team="platform", org_unit="ai-eng", cost_center="CC-1", tenant_tier="admin"),
        }
        result = _resolve_claims(["unknown-group"], mapping)
        assert result is None

    def test_returns_none_for_empty_groups(self) -> None:
        mapping = {
            "admins": GroupClaims(team="platform", org_unit="ai-eng", cost_center="CC-1", tenant_tier="admin"),
        }
        result = _resolve_claims([], mapping)
        assert result is None


# ── _build_claim_overrides ───────────────────────────────────────────────────


class TestBuildClaimOverrides:
    def test_builds_correct_structure(self) -> None:
        claims = GroupClaims(team="platform", org_unit="ai-eng", cost_center="CC-1", tenant_tier="admin")
        overrides = _build_claim_overrides(claims)
        assert len(overrides) == 4
        keys = [o["claimKey"] for o in overrides]
        assert "custom:team" in keys
        assert "custom:org_unit" in keys
        assert "custom:cost_center" in keys
        assert "custom:tenant_tier" in keys

    def test_values_match_claims(self) -> None:
        claims = GroupClaims(team="ml-eng", org_unit="research", cost_center="CC-9999", tenant_tier="premium")
        overrides = _build_claim_overrides(claims)
        by_key = {o["claimKey"]: o["claimValue"] for o in overrides}
        assert by_key["custom:team"] == "ml-eng"
        assert by_key["custom:org_unit"] == "research"
        assert by_key["custom:cost_center"] == "CC-9999"
        assert by_key["custom:tenant_tier"] == "premium"


# ── handler (end-to-end) ─────────────────────────────────────────────────────


def _make_trigger_event(
    user_name: str = "testuser",
    groups: list[str] | None = None,
    trigger_source: str = "TokenGeneration_HostedAuth",
) -> dict[str, Any]:
    """Build a minimal Cognito Pre-Token-Generation V2 trigger event."""
    return {
        "version": "2",
        "triggerSource": trigger_source,
        "region": "us-east-1",
        "userPoolId": "us-east-1_TestPool",
        "userName": user_name,
        "callerContext": {"awsSdkVersion": "3.0", "clientId": "test-client-id"},
        "request": {
            "userAttributes": {"sub": "abc-123", "email": "test@example.com", "email_verified": "true"},
            "groupConfiguration": {
                "groupsToOverride": groups,
                "iamRolesToOverride": None,
                "preferredRole": None,
            },
            "scopes": ["openid", "email"],
        },
        "response": {"claimsAndScopeOverrides": {}},
    }


GROUP_MAPPING_ENV = json.dumps(
    {
        "aws-ai-gateway-admins": {
            "team": "platform",
            "org_unit": "ai-engineering",
            "cost_center": "CC-1234",
            "tenant_tier": "admin",
        },
        "aws-ml-engineers": {
            "team": "ml-eng",
            "org_unit": "ai-engineering",
            "cost_center": "CC-5678",
            "tenant_tier": "standard",
        },
    }
)


class TestHandler:
    @patch.dict("os.environ", {"GROUP_MAPPING": GROUP_MAPPING_ENV})
    def test_injects_claims_for_matching_group(self) -> None:
        event = _make_trigger_event(groups=["aws-ai-gateway-admins"])
        result = handler(event)

        overrides = result["response"]["claimsAndScopeOverrides"]
        id_claims = overrides["idTokenGeneration"]["claimsToAddOrOverride"]
        access_claims = overrides["accessTokenGeneration"]["claimsToAddOrOverride"]

        # Both tokens get the same claims
        assert len(id_claims) == 4
        assert len(access_claims) == 4

        id_by_key = {c["claimKey"]: c["claimValue"] for c in id_claims}
        assert id_by_key["custom:team"] == "platform"
        assert id_by_key["custom:tenant_tier"] == "admin"

    @patch.dict("os.environ", {"GROUP_MAPPING": GROUP_MAPPING_ENV})
    def test_returns_event_unchanged_when_no_group_match(self) -> None:
        event = _make_trigger_event(groups=["unknown-group"])
        result = handler(event)
        # No claims injected
        overrides = result["response"].get("claimsAndScopeOverrides", {})
        assert "idTokenGeneration" not in overrides or overrides.get("idTokenGeneration") == {}

    @patch.dict("os.environ", {"GROUP_MAPPING": GROUP_MAPPING_ENV})
    def test_returns_event_unchanged_when_no_groups(self) -> None:
        event = _make_trigger_event(groups=None)
        result = handler(event)
        overrides = result["response"].get("claimsAndScopeOverrides", {})
        assert "idTokenGeneration" not in overrides or overrides.get("idTokenGeneration") == {}

    @patch.dict("os.environ", {"GROUP_MAPPING": "{}"})
    def test_returns_event_unchanged_when_empty_mapping(self) -> None:
        event = _make_trigger_event(groups=["aws-ai-gateway-admins"])
        result = handler(event)
        # Empty mapping means no claims injected
        overrides = result["response"].get("claimsAndScopeOverrides", {})
        assert "idTokenGeneration" not in overrides or overrides.get("idTokenGeneration") == {}

    @patch.dict("os.environ", {"GROUP_MAPPING": GROUP_MAPPING_ENV})
    def test_first_matching_group_wins(self) -> None:
        event = _make_trigger_event(groups=["aws-ml-engineers", "aws-ai-gateway-admins"])
        result = handler(event)

        overrides = result["response"]["claimsAndScopeOverrides"]
        id_claims = overrides["idTokenGeneration"]["claimsToAddOrOverride"]
        id_by_key = {c["claimKey"]: c["claimValue"] for c in id_claims}

        # ml-engineers comes first, so it wins
        assert id_by_key["custom:team"] == "ml-eng"
        assert id_by_key["custom:tenant_tier"] == "standard"

    def test_handles_invalid_event_gracefully(self) -> None:
        # Handler should not crash on garbage input
        result = handler({"garbage": True})
        assert isinstance(result, dict)

    @patch.dict("os.environ", {"GROUP_MAPPING": GROUP_MAPPING_ENV})
    def test_preserves_event_structure(self) -> None:
        event = _make_trigger_event(groups=["aws-ai-gateway-admins"])
        result = handler(event)

        # Core event fields should be preserved
        assert result["version"] == "2"
        assert result["userName"] == "testuser"
        assert result["userPoolId"] == "us-east-1_TestPool"
