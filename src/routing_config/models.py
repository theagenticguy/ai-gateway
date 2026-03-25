"""Pydantic v2 models for routing config validation."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

_WEIGHT_TOLERANCE_LOW = 0.99
_WEIGHT_TOLERANCE_HIGH = 1.01
_MIN_PATH_PARTS_WITH_NAME = 3


class StrategyMode(StrEnum):
    """Supported Portkey routing strategy modes."""

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
    virtual_key: str | None = Field(default=None, description="Portkey virtual key for this target")
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
    """Complete routing configuration for Portkey."""

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

    def to_portkey_config(self) -> dict[str, Any]:
        """Convert to Portkey-native JSON config format."""
        config: dict[str, Any] = {
            "strategy": {"mode": self.strategy.mode.value},
        }
        if self.strategy.on_status_codes:
            config["strategy"]["on_status_codes"] = self.strategy.on_status_codes
        if self.strategy.conditions:
            config["strategy"]["conditions"] = [c.model_dump(exclude_none=True) for c in self.strategy.conditions]

        targets = []
        for t in self.targets:
            target: dict[str, Any] = {"name": t.name, "provider": t.provider}
            if t.override_params:
                target["override_params"] = t.override_params
            if t.weight is not None:
                target["weight"] = t.weight
            if t.virtual_key:
                target["virtual_key"] = t.virtual_key
            if t.retry:
                target["retry"] = t.retry
            targets.append(target)

        config["targets"] = targets
        return config


class RoutingConfigSummary(BaseModel):
    """Summary of a routing config for list responses."""

    name: str
    mode: str
    target_count: int
    builtin: bool = Field(description="True if this is a built-in (read-only) config")
    description: str = ""
