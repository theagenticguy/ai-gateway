"""Tests for the routing config Lambda.

Covers model validation, custom-config CRUD operations, and handler routing
for all HTTP methods.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from routing_config.handler import (
    _config_name,
    _path_method,
    handler,
)
from routing_config.models import (
    RoutingCondition,
    RoutingConfig,
    RoutingConfigSummary,
    RoutingStrategy,
    RoutingTarget,
    StrategyMode,
)

# -- Helpers -------------------------------------------------------------------

ADMIN_SCOPE = "https://gateway.internal/admin"


def _make_jwt(claims: dict[str, Any]) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"{header}.{payload}.sig"


_ADMIN_JWT = _make_jwt({"sub": "admin-user", "scope": ADMIN_SCOPE})


def _make_function_url_event(
    method: str = "GET",
    path: str = "/routing/configs",
    body: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Build an API Gateway event with an admin bearer by default."""
    event: dict[str, Any] = {
        "requestContext": {"requestId": "rid-test", "http": {"method": method, "path": path}},
        "rawPath": path,
        "isBase64Encoded": False,
        "headers": {"authorization": f"Bearer {token or _ADMIN_JWT}"},
    }
    if body is not None:
        event["body"] = json.dumps(body)
    return event


# -- Model Tests ---------------------------------------------------------------


class TestRoutingTarget:
    def test_valid_target(self) -> None:
        target = RoutingTarget(
            name="bedrock-claude",
            provider="bedrock",
            override_params={"model": "anthropic.claude-sonnet-4-20250514-v1:0"},
            weight=0.5,
        )
        assert target.name == "bedrock-claude"
        assert target.weight == 0.5

    def test_weight_bounds(self) -> None:
        with pytest.raises(ValidationError):
            RoutingTarget(name="t", provider="p", weight=1.5)
        with pytest.raises(ValidationError):
            RoutingTarget(name="t", provider="p", weight=-0.1)

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RoutingTarget(name="", provider="bedrock")

    def test_optional_fields_default_none(self) -> None:
        target = RoutingTarget(name="t", provider="bedrock")
        assert target.weight is None
        assert target.virtual_key is None
        assert target.retry is None


class TestRoutingStrategy:
    def test_loadbalance(self) -> None:
        strategy = RoutingStrategy(mode=StrategyMode.LOADBALANCE, on_status_codes=[429, 500])
        assert strategy.mode == StrategyMode.LOADBALANCE
        assert 429 in strategy.on_status_codes

    def test_fallback(self) -> None:
        strategy = RoutingStrategy(mode=StrategyMode.FALLBACK)
        assert strategy.mode == StrategyMode.FALLBACK

    def test_conditional_with_conditions(self) -> None:
        strategy = RoutingStrategy(
            mode=StrategyMode.CONDITIONAL,
            conditions=[
                RoutingCondition(query={"max_tokens": {"$lte": 100}}, then="haiku-target"),
                RoutingCondition(default="sonnet-target"),
            ],
        )
        assert len(strategy.conditions) == 2


class TestRoutingConfig:
    def test_valid_loadbalance_config(self) -> None:
        config = RoutingConfig(
            strategy=RoutingStrategy(mode=StrategyMode.LOADBALANCE, on_status_codes=[429]),
            targets=[
                RoutingTarget(name="a", provider="bedrock", weight=0.6),
                RoutingTarget(name="b", provider="anthropic", weight=0.4),
            ],
        )
        assert len(config.targets) == 2

    def test_valid_fallback_config(self) -> None:
        config = RoutingConfig(
            strategy=RoutingStrategy(mode=StrategyMode.FALLBACK, on_status_codes=[429, 500]),
            targets=[
                RoutingTarget(name="primary", provider="bedrock"),
                RoutingTarget(name="fallback", provider="anthropic"),
            ],
        )
        assert config.strategy.mode == StrategyMode.FALLBACK

    def test_duplicate_target_names_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unique"):
            RoutingConfig(
                strategy=RoutingStrategy(mode=StrategyMode.FALLBACK),
                targets=[
                    RoutingTarget(name="same", provider="bedrock"),
                    RoutingTarget(name="same", provider="anthropic"),
                ],
            )

    def test_loadbalance_weights_must_sum_to_one(self) -> None:
        with pytest.raises(ValidationError, match=r"sum to 1\.0"):
            RoutingConfig(
                strategy=RoutingStrategy(mode=StrategyMode.LOADBALANCE),
                targets=[
                    RoutingTarget(name="a", provider="bedrock", weight=0.3),
                    RoutingTarget(name="b", provider="anthropic", weight=0.3),
                ],
            )

    def test_loadbalance_weights_close_to_one_accepted(self) -> None:
        config = RoutingConfig(
            strategy=RoutingStrategy(mode=StrategyMode.LOADBALANCE),
            targets=[
                RoutingTarget(name="a", provider="bedrock", weight=0.7),
                RoutingTarget(name="b", provider="anthropic", weight=0.3),
            ],
        )
        assert len(config.targets) == 2

    def test_conditional_invalid_target_ref(self) -> None:
        with pytest.raises(ValidationError, match="unknown target"):
            RoutingConfig(
                strategy=RoutingStrategy(
                    mode=StrategyMode.CONDITIONAL,
                    conditions=[RoutingCondition(then="nonexistent")],
                ),
                targets=[RoutingTarget(name="real", provider="bedrock")],
            )

    def test_empty_targets_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RoutingConfig(
                strategy=RoutingStrategy(mode=StrategyMode.FALLBACK),
                targets=[],
            )

    def test_metadata_defaults(self) -> None:
        config = RoutingConfig(
            strategy=RoutingStrategy(mode=StrategyMode.FALLBACK),
            targets=[RoutingTarget(name="a", provider="bedrock")],
        )
        assert config.metadata.version == 1
        assert config.metadata.created_by == "system"

    # ── agentgateway backend renderer (ADR-017) ──────────────────────────────

    def test_to_agentgateway_fallback_one_group_per_target(self) -> None:
        config = RoutingConfig(
            strategy=RoutingStrategy(mode=StrategyMode.FALLBACK, on_status_codes=[429, 500]),
            targets=[
                RoutingTarget(
                    name="primary",
                    provider="bedrock",
                    override_params={"model": "anthropic.claude-sonnet-4-20250514-v1:0"},
                ),
                RoutingTarget(
                    name="fallback",
                    provider="anthropic",
                    override_params={"model": "claude-sonnet-4-20250514"},
                ),
            ],
        )
        backend = config.to_agentgateway_backend()
        # fallback -> ordered priority groups, one target each
        assert len(backend["groups"]) == 2
        g0 = backend["groups"][0]["providers"][0]
        assert g0["name"] == "primary"
        assert g0["provider"]["bedrock"]["model"] == "anthropic.claude-sonnet-4-20250514-v1:0"
        # Bedrock gets ambient AWS SigV4 auth
        assert g0["policies"]["backendAuth"]["aws"] == {}
        g1 = backend["groups"][1]["providers"][0]
        assert g1["provider"]["anthropic"]["model"] == "claude-sonnet-4-20250514"

    def test_to_agentgateway_loadbalance_single_group(self) -> None:
        config = RoutingConfig(
            strategy=RoutingStrategy(mode=StrategyMode.LOADBALANCE),
            targets=[
                RoutingTarget(name="a", provider="bedrock", weight=0.6),
                RoutingTarget(name="b", provider="anthropic", weight=0.4),
            ],
        )
        backend = config.to_agentgateway_backend()
        # loadbalance -> one group with all providers
        assert len(backend["groups"]) == 1
        assert len(backend["groups"][0]["providers"]) == 2

    def test_to_agentgateway_provider_key_mapping(self) -> None:
        config = RoutingConfig(
            strategy=RoutingStrategy(mode=StrategyMode.FALLBACK),
            targets=[
                RoutingTarget(name="o", provider="openai", override_params={"model": "gpt-4o"}),
                RoutingTarget(name="z", provider="azure-openai"),
            ],
        )
        backend = config.to_agentgateway_backend()
        # openai -> openAI, azure-openai -> azure (agentgateway provider keys)
        assert "openAI" in backend["groups"][0]["providers"][0]["provider"]
        assert "azure" in backend["groups"][1]["providers"][0]["provider"]

    # ── migration warnings: lossy renders are surfaced, not silent ───────────

    def test_warnings_empty_for_clean_fallback(self) -> None:
        config = RoutingConfig(
            strategy=RoutingStrategy(mode=StrategyMode.FALLBACK),
            targets=[
                RoutingTarget(name="primary", provider="bedrock"),
                RoutingTarget(name="fallback", provider="anthropic"),
            ],
        )
        assert config.migration_warnings() == []

    def test_warning_for_conditional(self) -> None:
        config = RoutingConfig(
            strategy=RoutingStrategy(
                mode=StrategyMode.CONDITIONAL,
                conditions=[
                    RoutingCondition(query={"max_tokens": {"$lte": 100}}, then="small"),
                    RoutingCondition(default="big"),
                ],
            ),
            targets=[
                RoutingTarget(name="small", provider="bedrock"),
                RoutingTarget(name="big", provider="bedrock"),
            ],
        )
        warnings = config.migration_warnings()
        assert any("conditional routing has no agentgateway equivalent" in w for w in warnings)

    def test_warning_for_on_status_codes(self) -> None:
        config = RoutingConfig(
            strategy=RoutingStrategy(mode=StrategyMode.FALLBACK, on_status_codes=[429, 503]),
            targets=[RoutingTarget(name="a", provider="bedrock")],
        )
        warnings = config.migration_warnings()
        assert any("on_status_codes" in w for w in warnings)

    def test_warning_for_loadbalance_weights(self) -> None:
        config = RoutingConfig(
            strategy=RoutingStrategy(mode=StrategyMode.LOADBALANCE),
            targets=[
                RoutingTarget(name="a", provider="bedrock", weight=0.6),
                RoutingTarget(name="b", provider="anthropic", weight=0.4),
            ],
        )
        warnings = config.migration_warnings()
        assert any("weights" in w and "not honored" in w for w in warnings)

    def test_warning_for_per_target_retry_and_virtual_key(self) -> None:
        config = RoutingConfig(
            strategy=RoutingStrategy(mode=StrategyMode.FALLBACK),
            targets=[
                RoutingTarget(
                    name="a",
                    provider="bedrock",
                    retry={"attempts": 2},
                    virtual_key="vk-123",
                ),
            ],
        )
        warnings = config.migration_warnings()
        assert any("retry config" in w for w in warnings)
        assert any("virtual_key" in w for w in warnings)


class TestRoutingConfigSummary:
    def test_summary(self) -> None:
        summary = RoutingConfigSummary(
            name="test",
            mode="loadbalance",
            target_count=2,
            builtin=True,
            description="Test config",
        )
        dumped = summary.model_dump()
        assert dumped["builtin"] is True
        assert dumped["target_count"] == 2


# -- Handler utility tests -----------------------------------------------------


class TestExtractPathAndMethod:
    def test_standard_event(self) -> None:
        event = _make_function_url_event("GET", "/routing/configs")
        path, method = _path_method(event)
        assert path == "/routing/configs"
        assert method == "GET"

    def test_with_config_name(self) -> None:
        event = _make_function_url_event("DELETE", "/routing/configs/my-config")
        path, method = _path_method(event)
        assert path == "/routing/configs/my-config"
        assert method == "DELETE"

    def test_missing_context_defaults(self) -> None:
        _path, method = _path_method({})
        assert method == "GET"


class TestExtractConfigName:
    def test_list_path(self) -> None:
        assert _config_name("/routing/configs") is None

    def test_named_path(self) -> None:
        assert _config_name("/routing/configs/cost-optimized") == "cost-optimized"

    def test_trailing_slash(self) -> None:
        assert _config_name("/routing/configs/ab-test/") == "ab-test"

    def test_root_path(self) -> None:
        assert _config_name("/") is None

    def test_empty(self) -> None:
        assert _config_name("") is None


class TestAuthorization:
    def test_missing_auth_401(self) -> None:
        event = {"requestContext": {"http": {"method": "GET", "path": "/routing/configs"}}, "headers": {}}
        assert handler(event)["statusCode"] == 401

    def test_non_admin_403(self) -> None:
        token = _make_jwt({"sub": "u", "scope": "https://gateway.internal/invoke"})
        result = handler(_make_function_url_event("GET", "/routing/configs", token=token))
        assert result["statusCode"] == 403
        assert json.loads(result["body"])["error"]["code"] == "forbidden"


# -- Handler CRUD tests --------------------------------------------------------


class TestHandlerListConfigs:
    @patch("routing_config.handler._list_custom_configs")
    def test_list_returns_custom(self, mock_custom: Any) -> None:
        mock_custom.return_value = [
            {
                "config_name": "my-custom",
                "strategy_mode": "loadbalance",
                "target_count": 2,
                "description": "Custom LB",
            }
        ]

        event = _make_function_url_event("GET", "/routing/configs")
        result = handler(event)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["total"] == 1
        names = [c["name"] for c in body["configs"]]
        assert "my-custom" in names

    @patch("routing_config.handler._list_custom_configs")
    def test_list_dynamo_failure_returns_empty(self, mock_custom: Any) -> None:
        mock_custom.side_effect = ClientError(
            {"Error": {"Code": "ServiceUnavailable", "Message": "DDB down"}},
            "Scan",
        )

        event = _make_function_url_event("GET", "/routing/configs")
        result = handler(event)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["total"] == 0


class TestHandlerGetConfig:
    @patch("routing_config.handler._get_custom_config")
    def test_get_custom(self, mock_custom: Any) -> None:
        mock_custom.return_value = {"strategy": {"mode": "loadbalance"}, "targets": []}

        event = _make_function_url_event("GET", "/routing/configs/my-config")
        result = handler(event)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["builtin"] is False

    @patch("routing_config.handler._get_custom_config")
    def test_get_not_found(self, mock_custom: Any) -> None:
        mock_custom.return_value = None

        event = _make_function_url_event("GET", "/routing/configs/nonexistent")
        result = handler(event)

        assert result["statusCode"] == 404


class TestHandlerCreateConfig:
    @patch("routing_config.handler._put_custom_config")
    @patch("routing_config.handler._get_custom_config")
    def test_create_success(self, mock_existing: Any, mock_put: Any) -> None:
        mock_existing.return_value = None

        body = {
            "name": "my-new-config",
            "strategy": {"mode": "fallback", "on_status_codes": [429, 500]},
            "targets": [
                {"name": "primary", "provider": "bedrock"},
                {"name": "secondary", "provider": "anthropic"},
            ],
        }
        event = _make_function_url_event("POST", "/routing/configs", body)
        result = handler(event)

        assert result["statusCode"] == 201
        body_resp = json.loads(result["body"])
        assert body_resp["name"] == "my-new-config"
        mock_put.assert_called_once()

    @patch("routing_config.handler._get_custom_config")
    def test_create_conflict_with_existing_custom(self, mock_existing: Any) -> None:
        mock_existing.return_value = {"strategy": {"mode": "fallback"}}

        body = {
            "name": "existing-config",
            "strategy": {"mode": "fallback"},
            "targets": [{"name": "t", "provider": "bedrock"}],
        }
        event = _make_function_url_event("POST", "/routing/configs", body)
        result = handler(event)

        assert result["statusCode"] == 409

    @patch("routing_config.handler._get_custom_config")
    def test_create_missing_name(self, mock_existing: Any) -> None:
        body = {
            "strategy": {"mode": "fallback"},
            "targets": [{"name": "t", "provider": "bedrock"}],
        }
        event = _make_function_url_event("POST", "/routing/configs", body)
        result = handler(event)

        assert result["statusCode"] == 400

    @patch("routing_config.handler._get_custom_config")
    def test_create_invalid_config(self, mock_existing: Any) -> None:
        mock_existing.return_value = None

        body = {
            "name": "bad-config",
            "strategy": {"mode": "loadbalance"},
            "targets": [],
        }
        event = _make_function_url_event("POST", "/routing/configs", body)
        result = handler(event)

        assert result["statusCode"] == 400

    def test_create_invalid_json(self) -> None:
        event = {
            "requestContext": {"http": {"method": "POST", "path": "/routing/configs"}},
            "headers": {"authorization": f"Bearer {_ADMIN_JWT}"},
            "rawPath": "/routing/configs",
            "body": "not-json!!!",
            "isBase64Encoded": False,
        }
        result = handler(event)
        assert result["statusCode"] == 400


class TestHandlerUpdateConfig:
    @patch("routing_config.handler._put_custom_config")
    @patch("routing_config.handler._get_custom_config")
    def test_update_success(self, mock_existing: Any, mock_put: Any) -> None:
        mock_existing.return_value = {"strategy": {"mode": "fallback"}}

        body = {
            "strategy": {"mode": "loadbalance"},
            "targets": [
                {"name": "a", "provider": "bedrock", "weight": 0.6},
                {"name": "b", "provider": "anthropic", "weight": 0.4},
            ],
        }
        event = _make_function_url_event("PUT", "/routing/configs/my-config", body)
        result = handler(event)

        assert result["statusCode"] == 200
        mock_put.assert_called_once()

    @patch("routing_config.handler._get_custom_config")
    def test_update_not_found(self, mock_existing: Any) -> None:
        mock_existing.return_value = None

        body = {
            "strategy": {"mode": "fallback"},
            "targets": [{"name": "t", "provider": "bedrock"}],
        }
        event = _make_function_url_event("PUT", "/routing/configs/nonexistent", body)
        result = handler(event)

        assert result["statusCode"] == 404


class TestHandlerDeleteConfig:
    @patch("routing_config.handler._delete_custom_config")
    def test_delete_success(self, mock_delete: Any) -> None:
        mock_delete.return_value = True

        event = _make_function_url_event("DELETE", "/routing/configs/my-config")
        result = handler(event)

        assert result["statusCode"] == 200

    @patch("routing_config.handler._delete_custom_config")
    def test_delete_not_found(self, mock_delete: Any) -> None:
        mock_delete.return_value = False

        event = _make_function_url_event("DELETE", "/routing/configs/nonexistent")
        result = handler(event)

        assert result["statusCode"] == 404


class TestHandlerMethodNotAllowed:
    def test_patch_not_allowed(self) -> None:
        event = _make_function_url_event("PATCH", "/routing/configs/test")
        result = handler(event)
        assert result["statusCode"] == 404


def _client_error(op: str = "GetItem") -> ClientError:
    return ClientError({"Error": {"Code": "InternalServerError", "Message": "ddb down"}}, op)


class TestHandlerStorageErrors:
    """DynamoDB failures map to UpstreamError (502); the health route and the
    outer catch-all (500) round out the error-branch coverage."""

    def test_health_check(self) -> None:
        event = _make_function_url_event("GET", "/health")
        result = handler(event)
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["status"] == "healthy"

    @patch("routing_config.handler._get_custom_config")
    def test_get_storage_error(self, mock_custom: Any) -> None:
        mock_custom.side_effect = _client_error()
        result = handler(_make_function_url_event("GET", "/routing/configs/x"))
        assert result["statusCode"] == 502
        assert json.loads(result["body"])["error"]["code"] == "upstream_error"

    @patch("routing_config.handler._get_custom_config")
    def test_create_conflict_check_storage_error(self, mock_custom: Any) -> None:
        mock_custom.side_effect = _client_error()
        body = {"name": "c", "strategy": {"mode": "fallback"}, "targets": [{"name": "t", "provider": "bedrock"}]}
        result = handler(_make_function_url_event("POST", "/routing/configs", body))
        assert result["statusCode"] == 502

    @patch("routing_config.handler._put_custom_config")
    @patch("routing_config.handler._get_custom_config")
    def test_create_put_storage_error(self, mock_custom: Any, mock_put: Any) -> None:
        mock_custom.return_value = None
        mock_put.side_effect = _client_error("PutItem")
        body = {"name": "c", "strategy": {"mode": "fallback"}, "targets": [{"name": "t", "provider": "bedrock"}]}
        result = handler(_make_function_url_event("POST", "/routing/configs", body))
        assert result["statusCode"] == 502

    def test_update_invalid_config(self) -> None:
        body = {"strategy": {"mode": "loadbalance"}, "targets": []}
        result = handler(_make_function_url_event("PUT", "/routing/configs/x", body))
        assert result["statusCode"] == 400
        assert json.loads(result["body"])["error"]["code"] == "validation_failed"

    @patch("routing_config.handler._get_custom_config")
    def test_update_existence_check_storage_error(self, mock_custom: Any) -> None:
        mock_custom.side_effect = _client_error()
        body = {"strategy": {"mode": "fallback"}, "targets": [{"name": "t", "provider": "bedrock"}]}
        result = handler(_make_function_url_event("PUT", "/routing/configs/x", body))
        assert result["statusCode"] == 502

    @patch("routing_config.handler._put_custom_config")
    @patch("routing_config.handler._get_custom_config")
    def test_update_put_storage_error(self, mock_custom: Any, mock_put: Any) -> None:
        mock_custom.return_value = {"strategy": {"mode": "fallback"}}
        mock_put.side_effect = _client_error("PutItem")
        body = {"strategy": {"mode": "fallback"}, "targets": [{"name": "t", "provider": "bedrock"}]}
        result = handler(_make_function_url_event("PUT", "/routing/configs/x", body))
        assert result["statusCode"] == 502

    @patch("routing_config.handler._delete_custom_config")
    def test_delete_storage_error(self, mock_delete: Any) -> None:
        mock_delete.side_effect = _client_error("DeleteItem")
        result = handler(_make_function_url_event("DELETE", "/routing/configs/x"))
        assert result["statusCode"] == 502

    def test_create_body_not_object(self) -> None:
        event = _make_function_url_event("POST", "/routing/configs")
        event["body"] = "[1, 2, 3]"
        result = handler(event)
        assert result["statusCode"] == 400
        assert json.loads(result["body"])["error"]["code"] == "validation_failed"

    @patch("routing_config.handler._list_custom_configs")
    def test_unhandled_error_returns_500(self, mock_custom: Any) -> None:
        # A non-ClientError from the scan escapes _list_configs' ClientError catch
        # and trips the handler's outer catch-all -> 500.
        mock_custom.side_effect = RuntimeError("boom")
        result = handler(_make_function_url_event("GET", "/routing/configs"))
        assert result["statusCode"] == 500
        assert json.loads(result["body"])["error"]["code"] == "internal_error"


# -- Property-based tests ------------------------------------------------------


class TestPropertyBased:
    @given(
        name=st.text(min_size=1, max_size=128, alphabet=st.characters(categories=("L", "N", "Pd"))),
        provider=st.sampled_from(["bedrock", "anthropic", "openai", "azure-openai", "google"]),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_routing_target_never_crashes(self, name: str, provider: str) -> None:
        """RoutingTarget creation should not crash on valid inputs."""
        target = RoutingTarget(name=name, provider=provider)
        assert isinstance(target.name, str)
        assert isinstance(target.provider, str)

    @given(path=st.text(max_size=200))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_config_name_never_crashes(self, path: str) -> None:
        """Path extraction should never crash on any input."""
        result = _config_name(path)
        assert result is None or isinstance(result, str)

    @given(body_text=st.text(max_size=500))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_handler_never_crashes_on_random_body(self, body_text: str) -> None:
        """Handler should return a valid response for any body input."""
        event = {
            "requestContext": {"http": {"method": "POST", "path": "/routing/configs"}},
            "headers": {"authorization": f"Bearer {_ADMIN_JWT}"},
            "rawPath": "/routing/configs",
            "body": body_text,
            "isBase64Encoded": False,
        }
        with patch("routing_config.handler._get_custom_config", return_value=None):
            result = handler(event)
        assert "statusCode" in result
        assert result["statusCode"] in (200, 201, 400, 403, 404, 405, 409, 500)
