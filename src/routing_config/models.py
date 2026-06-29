"""Pydantic v2 models for routing config validation."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

_WEIGHT_TOLERANCE_LOW = 0.99
_WEIGHT_TOLERANCE_HIGH = 1.01
_MIN_PATH_PARTS_WITH_NAME = 3


class StrategyMode(StrEnum):
    """Supported routing strategy modes."""

    LOADBALANCE = "loadbalance"
    FALLBACK = "fallback"
    CONDITIONAL = "conditional"


class RoutingTarget(BaseModel):
    """A single target in a routing configuration."""

    name: str = Field(min_length=1, max_length=128, description="Unique target name within this config")
    provider: str = Field(min_length=1, description="Provider name: bedrock, anthropic, openai, azure-openai, google")
    override_params: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific overrides (e.g. model ID)",
    )
    weight: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Traffic weight for loadbalance mode (0.0-1.0)",
    )
    virtual_key: str | None = Field(default=None, description="Provider virtual-key reference for this target")
    retry: dict[str, Any] | None = Field(
        default=None,
        description="Per-target retry config (attempts, on_status_codes)",
    )


class RoutingCondition(BaseModel):
    """A condition entry for conditional routing."""

    query: dict[str, Any] | None = Field(default=None, description="Query predicate (e.g. max_tokens.$lte)")
    then: str | None = Field(default=None, description="Target name to route to if condition matches")
    default: str | None = Field(default=None, description="Default target name (used in the final condition)")


class RoutingStrategy(BaseModel):
    """Routing strategy configuration."""

    mode: StrategyMode = Field(description="Routing mode: loadbalance, fallback, or conditional")
    on_status_codes: list[int] = Field(
        default_factory=list,
        description="HTTP status codes that trigger failover/rebalance",
    )
    conditions: list[RoutingCondition] = Field(
        default_factory=list,
        description="Conditions for conditional mode routing",
    )


class ConfigMetadata(BaseModel):
    """Metadata about a routing configuration."""

    description: str = Field(default="", max_length=500)
    created_by: str = Field(default="system")
    created_at: str = Field(default="")
    updated_at: str = Field(default="")
    version: int = Field(default=1, ge=1)


class RoutingConfig(BaseModel):
    """Complete provider routing configuration."""

    strategy: RoutingStrategy
    targets: list[RoutingTarget] = Field(min_length=1, description="At least one routing target is required")
    metadata: ConfigMetadata = Field(default_factory=ConfigMetadata)

    @model_validator(mode="after")
    def validate_target_names_unique(self) -> RoutingConfig:
        """Ensure all target names are unique within a config."""
        names = [t.name for t in self.targets]
        if len(names) != len(set(names)):
            msg = "Target names must be unique within a routing config"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def validate_weights_for_loadbalance(self) -> RoutingConfig:
        """For loadbalance mode, validate that weights are provided and sum to ~1.0."""
        if self.strategy.mode == StrategyMode.LOADBALANCE:
            weights = [t.weight for t in self.targets if t.weight is not None]
            if weights and len(weights) == len(self.targets):
                total = sum(weights)
                if not (_WEIGHT_TOLERANCE_LOW <= total <= _WEIGHT_TOLERANCE_HIGH):
                    msg = f"Target weights must sum to 1.0 for loadbalance mode (got {total:.2f})"
                    raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def validate_conditional_targets_exist(self) -> RoutingConfig:
        """For conditional mode, validate that referenced targets exist."""
        if self.strategy.mode == StrategyMode.CONDITIONAL:
            target_names = {t.name for t in self.targets}
            for condition in self.strategy.conditions:
                if condition.then and condition.then not in target_names:
                    msg = f"Condition references unknown target: {condition.then}"
                    raise ValueError(msg)
                if condition.default and condition.default not in target_names:
                    msg = f"Default condition references unknown target: {condition.default}"
                    raise ValueError(msg)
        return self

    def to_agentgateway_backend(self) -> dict[str, Any]:
        """Render this routing config as an agentgateway AI-backend block (ADR-017).

        Maps the routing strategy onto agentgateway's ``ai.groups`` priority
        tiers (the shape under ``backends: - ai:``):

        - **fallback**: each target becomes its own priority group, in order.
          agentgateway tries group 0, then group 1, etc., which reproduces an
          ordered fallback chain. The ``on_status_codes`` trigger has no
          per-edge equivalent; agentgateway fails over on connection/health
          eviction, so the mapping is documented as approximate.
        - **loadbalance**: all targets in ONE group; agentgateway load-balances
          across a group with power-of-two-choices. Per-target ``weight`` is
          carried but agentgateway weighting is capacity-based, not 0-1 ratios.
        - **conditional**: agentgateway has no request-field predicate routing
          (e.g. on max_tokens), so the conditions are dropped and the targets
          collapse to a single priority-ordered fallback. This is a known gap
          (ADR-017); conditional configs should be flagged on migration.

        Returns the value to place under a route's ``backends: - ai:`` key.
        """
        provider_key = {
            "bedrock": "bedrock",
            "anthropic": "anthropic",
            "openai": "openAI",
            "azure-openai": "azure",
            "azure": "azure",
            "google": "gemini",
        }

        def provider_block(target: RoutingTarget) -> dict[str, Any]:
            key = provider_key.get(target.provider, target.provider)
            spec: dict[str, Any] = {}
            model = target.override_params.get("model")
            if model:
                spec["model"] = model
            entry: dict[str, Any] = {"name": target.name, "provider": {key: spec}}
            if key == "bedrock":
                # Bedrock uses ambient ECS task-role creds + SigV4.
                entry["policies"] = {"backendAuth": {"aws": {}}}
            return entry

        if self.strategy.mode == StrategyMode.LOADBALANCE:
            groups = [{"providers": [provider_block(t) for t in self.targets]}]
        else:
            # fallback + conditional both collapse to ordered priority groups.
            groups = [{"providers": [provider_block(t)]} for t in self.targets]

        return {"groups": groups}


class RoutingConfigSummary(BaseModel):
    """Summary of a routing config for list responses."""

    name: str
    mode: str
    target_count: int
    builtin: bool = Field(description="True if this is a built-in (read-only) config")
    description: str = ""
