"""Routing config Lambda (Function URL).

Serves routing configurations dynamically:
- Built-in configs are loaded from the portkey-configs directory (read-only).
- Custom configs are stored in and served from DynamoDB.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError
from pydantic import ValidationError

from routing_config.models import (
    _MIN_PATH_PARTS_WITH_NAME,
    RoutingConfig,
    RoutingConfigSummary,
)

logger = logging.getLogger("routing_config")
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
            name = config_file.stem
            configs[name] = data
        except (json.JSONDecodeError, OSError):
            logger.exception("Failed to load config file: %s", config_file)

    return configs


_BUILTIN_CONFIGS: dict[str, dict[str, Any]] | None = None


def _get_builtin_configs() -> dict[str, dict[str, Any]]:
    """Get built-in configs (cached after first load)."""
    global _BUILTIN_CONFIGS  # noqa: PLW0603
    if _BUILTIN_CONFIGS is None:
        _BUILTIN_CONFIGS = _load_builtin_configs()
    return _BUILTIN_CONFIGS


# -- DynamoDB helpers ----------------------------------------------------------


def _get_custom_config(name: str) -> dict[str, Any] | None:
    """Fetch a custom routing config from DynamoDB."""
    table = dynamodb.Table(CONFIGS_TABLE)
    resp = table.get_item(Key={"config_name": name})
    item = resp.get("Item")
    if not item:
        return None
    return json.loads(item["config_json"]) if isinstance(item.get("config_json"), str) else item.get("config_json")


def _list_custom_configs() -> list[dict[str, Any]]:
    """List all custom routing configs from DynamoDB."""
    table = dynamodb.Table(CONFIGS_TABLE)
    resp = table.scan(
        ProjectionExpression="config_name, strategy_mode, target_count, description, created_by, updated_at",
    )
    return resp.get("Items", [])


def _put_custom_config(name: str, config: RoutingConfig) -> None:
    """Store a custom routing config in DynamoDB."""
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
    """Delete a custom routing config from DynamoDB. Returns True if it existed."""
    table = dynamodb.Table(CONFIGS_TABLE)
    try:
        table.delete_item(
            Key={"config_name": name},
            ConditionExpression="attribute_exists(config_name)",
        )
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "ConditionalCheckFailedException":
            return False
        raise
    else:
        return True


# -- Route handling ------------------------------------------------------------


def _extract_path_and_method(event: dict[str, Any]) -> tuple[str, str]:
    """Extract HTTP method and path from a Lambda Function URL event."""
    request_context = event.get("requestContext", {})
    http = request_context.get("http", {})
    method = http.get("method", "GET").upper()
    raw_path = http.get("path", event.get("rawPath", "/"))
    return raw_path, method


def _parse_body(event: dict[str, Any]) -> dict[str, Any]:
    """Parse JSON body from Lambda Function URL event."""
    body_str = event.get("body", "{}")
    if event.get("isBase64Encoded"):
        import base64  # noqa: PLC0415

        body_str = base64.b64decode(body_str).decode()
    return json.loads(body_str) if isinstance(body_str, str) else body_str


def _extract_config_name(path: str) -> str | None:
    """Extract config name from path (e.g. /routing/configs/cost-optimized)."""
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) >= _MIN_PATH_PARTS_WITH_NAME and parts[0] == "routing" and parts[1] == "configs":
        return parts[2]
    return None


def _handle_list_configs() -> dict[str, Any]:
    """GET /routing/configs -- list all available routing strategies."""
    builtin = _get_builtin_configs()
    summaries: list[dict[str, Any]] = []

    for name, config_data in sorted(builtin.items()):
        strategy = config_data.get("strategy", {})
        targets = config_data.get("targets", [])
        summaries.append(
            RoutingConfigSummary(
                name=name,
                mode=strategy.get("mode", "unknown"),
                target_count=len(targets),
                builtin=True,
                description=f"Built-in {strategy.get('mode', '')} routing config",
            ).model_dump()
        )

    try:
        custom_items = _list_custom_configs()
        summaries.extend(
            RoutingConfigSummary(
                name=item["config_name"],
                mode=item.get("strategy_mode", "unknown"),
                target_count=int(item.get("target_count", 0)),
                builtin=False,
                description=item.get("description", ""),
            ).model_dump()
            for item in custom_items
        )
    except Exception:
        logger.exception("Failed to list custom configs from DynamoDB")

    return _build_response(200, {"configs": summaries, "total": len(summaries)})


def _handle_get_config(name: str) -> dict[str, Any]:
    """GET /routing/configs/{name} -- get a specific config."""
    builtin = _get_builtin_configs()
    if name in builtin:
        return _build_response(
            200,
            {
                "name": name,
                "builtin": True,
                "config": builtin[name],
            },
        )

    try:
        custom = _get_custom_config(name)
        if custom:
            return _build_response(
                200,
                {
                    "name": name,
                    "builtin": False,
                    "config": custom,
                },
            )
    except Exception:
        logger.exception("Failed to fetch custom config: %s", name)
        return _build_response(500, {"error": "Failed to fetch config from storage"})

    return _build_response(404, {"error": f"Routing config not found: {name}"})


def _handle_create_config(event: dict[str, Any]) -> dict[str, Any]:  # noqa: PLR0911
    """POST /routing/configs -- create a new custom config."""
    try:
        body = _parse_body(event)
    except Exception:
        return _build_response(400, {"error": "Invalid JSON body"})

    if not isinstance(body, dict):
        return _build_response(400, {"error": "Request body must be a JSON object"})

    name = body.pop("name", None)
    if not name or not isinstance(name, str):
        return _build_response(400, {"error": "Missing required field: name"})

    if name in _get_builtin_configs():
        return _build_response(409, {"error": f"Cannot create config with built-in name: {name}"})

    try:
        existing = _get_custom_config(name)
        if existing:
            return _build_response(409, {"error": f"Config already exists: {name}. Use PUT to update."})
    except Exception:
        logger.exception("Failed to check existing config: %s", name)
        return _build_response(500, {"error": "Storage error during conflict check"})

    try:
        config = RoutingConfig.model_validate(body)
    except ValidationError as e:
        return _build_response(400, {"error": f"Validation failed: {e.error_count()} errors", "details": e.errors()})

    now = datetime.now(tz=UTC).isoformat()
    config.metadata.created_at = now
    config.metadata.updated_at = now

    try:
        _put_custom_config(name, config)
    except Exception:
        logger.exception("Failed to store config: %s", name)
        return _build_response(500, {"error": "Failed to store config"})

    logger.info("Created custom routing config: %s", name)
    return _build_response(
        201,
        {
            "name": name,
            "config": config.to_portkey_config(),
            "message": "Config created successfully",
        },
    )


def _handle_update_config(name: str, event: dict[str, Any]) -> dict[str, Any]:  # noqa: PLR0911
    """PUT /routing/configs/{name} -- update a custom config."""
    if name in _get_builtin_configs():
        return _build_response(403, {"error": f"Cannot modify built-in config: {name}"})

    try:
        body = _parse_body(event)
    except Exception:
        return _build_response(400, {"error": "Invalid JSON body"})

    if not isinstance(body, dict):
        return _build_response(400, {"error": "Request body must be a JSON object"})

    try:
        config = RoutingConfig.model_validate(body)
    except ValidationError as e:
        return _build_response(400, {"error": f"Validation failed: {e.error_count()} errors", "details": e.errors()})

    try:
        existing = _get_custom_config(name)
        if not existing:
            return _build_response(404, {"error": f"Config not found: {name}. Use POST to create."})
    except Exception:
        logger.exception("Failed to check config existence: %s", name)
        return _build_response(500, {"error": "Storage error during existence check"})

    now = datetime.now(tz=UTC).isoformat()
    config.metadata.updated_at = now
    config.metadata.version += 1

    try:
        _put_custom_config(name, config)
    except Exception:
        logger.exception("Failed to update config: %s", name)
        return _build_response(500, {"error": "Failed to update config"})

    logger.info("Updated custom routing config: %s (v%d)", name, config.metadata.version)
    return _build_response(
        200,
        {
            "name": name,
            "config": config.to_portkey_config(),
            "message": "Config updated successfully",
        },
    )


def _handle_delete_config(name: str) -> dict[str, Any]:
    """DELETE /routing/configs/{name} -- delete a custom config."""
    if name in _get_builtin_configs():
        return _build_response(403, {"error": f"Cannot delete built-in config: {name}"})

    try:
        deleted = _delete_custom_config(name)
    except Exception:
        logger.exception("Failed to delete config: %s", name)
        return _build_response(500, {"error": "Failed to delete config"})

    if not deleted:
        return _build_response(404, {"error": f"Config not found: {name}"})

    logger.info("Deleted custom routing config: %s", name)
    return _build_response(200, {"message": f"Config deleted: {name}"})


# -- Response builder ----------------------------------------------------------


def _build_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    """Build a Lambda Function URL response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


# -- Lambda entry point --------------------------------------------------------


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Lambda Function URL handler for routing config CRUD.

    Routes:
        GET  /routing/configs         -- list all configs
        GET  /routing/configs/{name}  -- get a specific config
        POST /routing/configs         -- create a custom config
        PUT  /routing/configs/{name}  -- update a custom config
        DELETE /routing/configs/{name} -- delete a custom config
    """
    path, method = _extract_path_and_method(event)
    config_name = _extract_config_name(path)

    if method == "GET" and config_name is None:
        return _handle_list_configs()

    if method == "GET" and config_name is not None:
        return _handle_get_config(config_name)

    if method == "POST" and config_name is None:
        return _handle_create_config(event)

    if method == "PUT" and config_name is not None:
        return _handle_update_config(config_name, event)

    if method == "DELETE" and config_name is not None:
        return _handle_delete_config(config_name)

    return _build_response(405, {"error": f"Method not allowed: {method} {path}"})
