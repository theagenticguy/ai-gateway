"""Routing config Lambda — Portkey routing strategies, migrated onto gwcore (ADR-016).

Built-in configs load from the portkey-configs directory (read-only); custom
configs live in DynamoDB. Authorization is now enforced in-handler (it was not):
every request requires the admin scope, and the create / update / delete
mutations emit audit events.

Routes:
    GET    /routing/configs         -- list all configs
    GET    /routing/configs/{name}  -- get a specific config
    POST   /routing/configs         -- create a custom config
    PUT    /routing/configs/{name}  -- update a custom config
    DELETE /routing/configs/{name}  -- delete a custom config
"""

from __future__ import annotations

import contextlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError
from pydantic import ValidationError

from gwcore import audit, auth, errors, ok, responses
from gwcore.logging import bind, correlation_id, get_logger
from gwcore.responses import request_body
from gwcore.telemetry import Timer, emit_metric
from routing_config.models import (
    _MIN_PATH_PARTS_WITH_NAME,
    RoutingConfig,
    RoutingConfigSummary,
)

logger = get_logger("routing_config")

CONFIGS_TABLE = os.environ.get("ROUTING_CONFIGS_TABLE", "gateway-routing-configs")
CONFIGS_DIR = os.environ.get(
    "PORTKEY_CONFIGS_DIR",
    str(Path(__file__).resolve().parent.parent.parent / "infrastructure" / "portkey-configs"),
)

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))


# -- Built-in config loader ---------------------------------------------------


def _load_builtin_configs() -> dict[str, dict[str, Any]]:
    """Load all JSON files from the portkey-configs directory."""
    configs: dict[str, dict[str, Any]] = {}
    configs_path = Path(CONFIGS_DIR)
    if not configs_path.is_dir():
        logger.warning("Portkey configs directory not found: %s", CONFIGS_DIR)
        return configs

    for config_file in sorted(configs_path.glob("*.json")):
        try:
            data = json.loads(config_file.read_text())
            configs[config_file.stem] = data
        except (json.JSONDecodeError, OSError):
            logger.exception("Failed to load config file: %s", config_file)
    return configs


_BUILTIN_CONFIGS: dict[str, dict[str, Any]] | None = None


def _get_builtin_configs() -> dict[str, dict[str, Any]]:
    """Get built-in configs (cached after first load)."""
    global _BUILTIN_CONFIGS  # noqa: PLW0603 — module-scoped cache for warm Lambda
    if _BUILTIN_CONFIGS is None:
        _BUILTIN_CONFIGS = _load_builtin_configs()
    return _BUILTIN_CONFIGS


# -- DynamoDB helpers ----------------------------------------------------------


def _get_custom_config(name: str) -> dict[str, Any] | None:
    table = dynamodb.Table(CONFIGS_TABLE)
    item = table.get_item(Key={"config_name": name}).get("Item")
    if not item:
        return None
    cfg = item.get("config_json")
    return json.loads(cfg) if isinstance(cfg, str) else cfg


def _list_custom_configs() -> list[dict[str, Any]]:
    table = dynamodb.Table(CONFIGS_TABLE)
    resp = table.scan(
        ProjectionExpression="config_name, strategy_mode, target_count, description, created_by, updated_at",
    )
    return resp.get("Items", [])


def _put_custom_config(name: str, config: RoutingConfig) -> None:
    table = dynamodb.Table(CONFIGS_TABLE)
    now = datetime.now(tz=UTC).isoformat()
    table.put_item(
        Item={
            "config_name": name,
            "config_json": json.dumps(config.to_portkey_config()),
            "strategy_mode": config.strategy.mode.value,
            "target_count": len(config.targets),
            "description": config.metadata.description,
            "created_by": config.metadata.created_by,
            "created_at": config.metadata.created_at or now,
            "updated_at": now,
            "version": config.metadata.version,
        },
    )


def _delete_custom_config(name: str) -> bool:
    """Delete a custom routing config. Returns True if it existed."""
    table = dynamodb.Table(CONFIGS_TABLE)
    try:
        table.delete_item(Key={"config_name": name}, ConditionExpression="attribute_exists(config_name)")
    except ClientError as e:
        if e.response.get("Error", {}).get("Code", "") == "ConditionalCheckFailedException":
            return False
        raise
    return True


# -- Route helpers -------------------------------------------------------------


def _path_method(event: dict[str, Any]) -> tuple[str, str]:
    http = event.get("requestContext", {}).get("http", {})
    method = event.get("httpMethod") or http.get("method", "GET")
    path = http.get("path") or event.get("path") or event.get("rawPath", "/")
    return str(path), str(method).upper()


def _config_name(path: str) -> str | None:
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) >= _MIN_PATH_PARTS_WITH_NAME and parts[0] == "routing" and parts[1] == "configs":
        return parts[2]
    return None


def _audit(event: dict[str, Any], principal: auth.Principal, **kw: Any) -> None:
    audit.emit(audit.event_from_request(event, actor=principal.sub, team=principal.team, **kw))


def _validate_body(event: dict[str, Any]) -> dict[str, Any]:
    try:
        body = json.loads(request_body(event))
    except (json.JSONDecodeError, ValueError) as e:
        raise errors.ValidationFailedError("Invalid JSON body") from e
    if not isinstance(body, dict):
        raise errors.ValidationFailedError("Request body must be a JSON object")
    return body


# -- Route handlers ------------------------------------------------------------


def _list_configs() -> dict[str, Any]:
    """GET /routing/configs — built-in + custom config summaries."""
    summaries: list[dict[str, Any]] = []
    for name, config_data in sorted(_get_builtin_configs().items()):
        strategy = config_data.get("strategy", {})
        summaries.append(
            RoutingConfigSummary(
                name=name,
                mode=strategy.get("mode", "unknown"),
                target_count=len(config_data.get("targets", [])),
                builtin=True,
                description=f"Built-in {strategy.get('mode', '')} routing config",
            ).model_dump()
        )
    try:
        summaries.extend(
            RoutingConfigSummary(
                name=item["config_name"],
                mode=item.get("strategy_mode", "unknown"),
                target_count=int(item.get("target_count", 0)),
                builtin=False,
                description=item.get("description", ""),
            ).model_dump()
            for item in _list_custom_configs()
        )
    except ClientError:
        logger.exception("Failed to list custom configs from DynamoDB")
    return ok({"configs": summaries, "total": len(summaries)})


def _get_config(name: str) -> dict[str, Any]:
    """GET /routing/configs/{name} — built-in first, then custom."""
    builtin = _get_builtin_configs()
    if name in builtin:
        return ok({"name": name, "builtin": True, "config": builtin[name]})
    try:
        custom = _get_custom_config(name)
    except ClientError as e:
        raise errors.UpstreamError("Failed to fetch config from storage") from e
    if custom:
        return ok({"name": name, "builtin": False, "config": custom})
    raise errors.NotFoundError(f"Routing config not found: {name}")


def _create_config(event: dict[str, Any], principal: auth.Principal) -> dict[str, Any]:
    """POST /routing/configs — create a new custom config."""
    body = _validate_body(event)
    name = body.pop("name", None)
    if not name or not isinstance(name, str):
        raise errors.ValidationFailedError("Missing required field: name")
    if name in _get_builtin_configs():
        raise errors.ConflictError(f"Cannot create config with built-in name: {name}")
    try:
        if _get_custom_config(name):
            raise errors.ConflictError(f"Config already exists: {name}. Use PUT to update.")
    except ClientError as e:
        raise errors.UpstreamError("Storage error during conflict check") from e

    try:
        config = RoutingConfig.model_validate(body)
    except ValidationError as e:
        raise errors.ValidationFailedError("Invalid routing config", details={"errors": e.errors()}) from e

    now = datetime.now(tz=UTC).isoformat()
    config.metadata.created_at = now
    config.metadata.updated_at = now
    try:
        _put_custom_config(name, config)
    except ClientError as e:
        raise errors.UpstreamError("Failed to store config") from e

    logger.info("Created custom routing config: %s", name)
    _audit(event, principal, action="routing.create", resource=name, status=201)
    return ok(
        {"name": name, "config": config.to_portkey_config(), "message": "Config created successfully"},
        status=201,
    )


def _update_config(name: str, event: dict[str, Any], principal: auth.Principal) -> dict[str, Any]:
    """PUT /routing/configs/{name} — update a custom config."""
    if name in _get_builtin_configs():
        raise errors.ForbiddenError(f"Cannot modify built-in config: {name}")
    body = _validate_body(event)
    try:
        config = RoutingConfig.model_validate(body)
    except ValidationError as e:
        raise errors.ValidationFailedError("Invalid routing config", details={"errors": e.errors()}) from e

    try:
        if not _get_custom_config(name):
            raise errors.NotFoundError(f"Config not found: {name}. Use POST to create.")
    except ClientError as e:
        raise errors.UpstreamError("Storage error during existence check") from e

    config.metadata.updated_at = datetime.now(tz=UTC).isoformat()
    config.metadata.version += 1
    try:
        _put_custom_config(name, config)
    except ClientError as e:
        raise errors.UpstreamError("Failed to update config") from e

    logger.info("Updated custom routing config: %s (v%d)", name, config.metadata.version)
    _audit(event, principal, action="routing.update", resource=name, detail=f"v{config.metadata.version}")
    return ok({"name": name, "config": config.to_portkey_config(), "message": "Config updated successfully"})


def _delete_config(name: str, event: dict[str, Any], principal: auth.Principal) -> dict[str, Any]:
    """DELETE /routing/configs/{name} — delete a custom config."""
    if name in _get_builtin_configs():
        raise errors.ForbiddenError(f"Cannot delete built-in config: {name}")
    try:
        deleted = _delete_custom_config(name)
    except ClientError as e:
        raise errors.UpstreamError("Failed to delete config") from e
    if not deleted:
        raise errors.NotFoundError(f"Config not found: {name}")

    logger.info("Deleted custom routing config: %s", name)
    _audit(event, principal, action="routing.delete", resource=name)
    return ok({"message": f"Config deleted: {name}"})


# -- Lambda entry point --------------------------------------------------------


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:  # noqa: PLR0911 — one return per CRUD route
    """Lambda handler for routing config CRUD (admin scope required)."""
    cid = correlation_id(event)
    log = bind(logger, cid)
    path, method = _path_method(event)

    if path == "/health" and method == "GET":
        return ok({"status": "healthy"})

    try:
        with Timer("RequestLatency", route="routing_config"):
            principal = auth.build_principal(event)
            auth.require(principal, scopes=[auth.ADMIN_SCOPE])
            name = _config_name(path)

            if method == "GET" and name is None:
                return _list_configs()
            if method == "GET" and name is not None:
                return _get_config(name)
            if method == "POST" and name is None:
                return _create_config(event, principal)
            if method == "PUT" and name is not None:
                return _update_config(name, event, principal)
            if method == "DELETE" and name is not None:
                return _delete_config(name, event, principal)
            raise errors.NotFoundError(f"Not found: {method} {path}")  # noqa: TRY301 — dispatch fallthrough
    except errors.ControlPlaneError as exc:
        if exc.status in {401, 403}:
            emit_metric("AuthzDenied", 1, dimensions={"Route": "routing_config"})
            actor = "unknown"
            with contextlib.suppress(errors.ControlPlaneError):
                actor = auth.build_principal(event).sub or "unknown"
            audit.emit(
                audit.event_from_request(
                    event,
                    action="routing.access",
                    actor=actor,
                    resource=f"{method} {path}",
                    decision="deny",
                    status=exc.status,
                    detail=exc.code,
                )
            )
        return responses.error_response(exc)
    except Exception:
        log.exception("Unhandled error in routing_config: %s %s", method, path)
        emit_metric("RoutingConfigError", 1, dimensions={"Code": "internal_error"})
        return responses.error_response(errors.ControlPlaneError("Internal error"))
