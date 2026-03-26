"""Pricing admin Lambda (Function URL).

Manages dynamic pricing overrides stored in DynamoDB.
Static pricing from cost_attribution.pricing serves as the fallback
when no DynamoDB entry exists for a given provider/model pair.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError
from pydantic import ValidationError

from cost_attribution.pricing import PRICING_TABLE
from pricing_admin.models import PriceEntry, PriceSummary

logger = logging.getLogger("pricing_admin")
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

PRICING_TABLE_NAME = os.environ.get("PRICING_TABLE_NAME", "gateway-pricing")

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))

# Minimum path parts for provider/model extraction: /pricing/{provider}/{model} → 3
_MIN_PATH_PARTS_WITH_PROVIDER_MODEL = 3


# -- DynamoDB helpers ----------------------------------------------------------


def _make_pk(provider: str, model: str) -> str:
    """Build the partition key for a pricing entry."""
    return f"PRICE#{provider}#{model}"


def _get_price_item(provider: str, model: str) -> dict[str, Any] | None:
    """Fetch a single pricing entry from DynamoDB."""
    table = dynamodb.Table(PRICING_TABLE_NAME)
    resp = table.get_item(Key={"PK": _make_pk(provider, model), "SK": "CONFIG"})
    return resp.get("Item")


def _list_price_items() -> list[dict[str, Any]]:
    """List all pricing entries from DynamoDB."""
    table = dynamodb.Table(PRICING_TABLE_NAME)
    resp = table.scan(
        FilterExpression="SK = :sk",
        ExpressionAttributeValues={":sk": "CONFIG"},
    )
    return resp.get("Items", [])


def _put_price_item(entry: PriceEntry) -> None:
    """Store a pricing entry in DynamoDB."""
    table = dynamodb.Table(PRICING_TABLE_NAME)
    now = datetime.now(tz=UTC).isoformat()
    item: dict[str, Any] = {
        "PK": _make_pk(entry.provider, entry.model),
        "SK": "CONFIG",
        "provider": entry.provider,
        "model": entry.model,
        "input_per_1k": Decimal(str(entry.input_per_1k)),
        "output_per_1k": Decimal(str(entry.output_per_1k)),
        "updated_at": now,
    }
    if entry.cache_read_per_1k is not None:
        item["cache_read_per_1k"] = Decimal(str(entry.cache_read_per_1k))
    if entry.cache_write_per_1k is not None:
        item["cache_write_per_1k"] = Decimal(str(entry.cache_write_per_1k))
    table.put_item(Item=item)


def _delete_price_item(provider: str, model: str) -> bool:
    """Delete a pricing entry from DynamoDB. Returns True if it existed."""
    table = dynamodb.Table(PRICING_TABLE_NAME)
    try:
        table.delete_item(
            Key={"PK": _make_pk(provider, model), "SK": "CONFIG"},
            ConditionExpression="attribute_exists(PK)",
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


def _extract_provider_model(path: str) -> tuple[str | None, str | None]:
    """Extract provider and model from path (e.g. /pricing/{provider}/{model}).

    Returns (provider, model) or (None, None) if path doesn't contain both.
    """
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) >= _MIN_PATH_PARTS_WITH_PROVIDER_MODEL and parts[0] == "pricing":
        return parts[1], parts[2]
    return None, None


def _handle_list_prices() -> dict[str, Any]:
    """GET /pricing -- list all prices (DynamoDB + static merged)."""
    summaries: list[dict[str, Any]] = []

    # Start with static entries
    seen: set[tuple[str, str]] = set()

    # Load DynamoDB entries first (they take priority)
    try:
        ddb_items = _list_price_items()
        for item in ddb_items:
            provider = item.get("provider", "")
            model = item.get("model", "")
            key = (provider, model)
            seen.add(key)
            summaries.append(
                PriceSummary(
                    provider=provider,
                    model=model,
                    input_per_1k=float(item.get("input_per_1k", 0)),
                    output_per_1k=float(item.get("output_per_1k", 0)),
                    source="dynamodb",
                ).model_dump()
            )
    except Exception:
        logger.exception("Failed to list pricing entries from DynamoDB")

    # Add static entries that aren't overridden
    for (provider, model), token_price in sorted(PRICING_TABLE.items()):
        key = (provider, model)
        if key not in seen:
            summaries.append(
                PriceSummary(
                    provider=provider,
                    model=model,
                    input_per_1k=token_price.input_per_1k,
                    output_per_1k=token_price.output_per_1k,
                    source="static",
                ).model_dump()
            )

    return _build_response(200, {"prices": summaries, "total": len(summaries)})


def _handle_get_price(provider: str, model: str) -> dict[str, Any]:
    """GET /pricing/{provider}/{model} -- get a specific price."""
    # Check DynamoDB first
    try:
        item = _get_price_item(provider, model)
        if item:
            entry = PriceEntry(
                provider=item.get("provider", provider),
                model=item.get("model", model),
                input_per_1k=float(item.get("input_per_1k", 0)),
                output_per_1k=float(item.get("output_per_1k", 0)),
                cache_read_per_1k=(
                    float(item["cache_read_per_1k"]) if item.get("cache_read_per_1k") is not None else None
                ),
                cache_write_per_1k=(
                    float(item["cache_write_per_1k"]) if item.get("cache_write_per_1k") is not None else None
                ),
                updated_at=item.get("updated_at", ""),
            )
            return _build_response(
                200,
                {
                    "source": "dynamodb",
                    "price": entry.model_dump(),
                },
            )
    except Exception:
        logger.exception("Failed to fetch price from DynamoDB: %s/%s", provider, model)
        return _build_response(500, {"error": "Failed to fetch price from storage"})

    # Fall back to static table
    static_price = PRICING_TABLE.get((provider, model))
    if static_price:
        entry = PriceEntry(
            provider=provider,
            model=model,
            input_per_1k=static_price.input_per_1k,
            output_per_1k=static_price.output_per_1k,
            cache_read_per_1k=static_price.cache_read_per_1k,
            cache_write_per_1k=static_price.cache_write_per_1k,
        )
        return _build_response(
            200,
            {
                "source": "static",
                "price": entry.model_dump(),
            },
        )

    return _build_response(404, {"error": f"Price not found: {provider}/{model}"})


def _handle_upsert_price(provider: str, model: str, event: dict[str, Any]) -> dict[str, Any]:
    """PUT /pricing/{provider}/{model} -- upsert a pricing entry."""
    try:
        body = _parse_body(event)
    except Exception:
        return _build_response(400, {"error": "Invalid JSON body"})

    if not isinstance(body, dict):
        return _build_response(400, {"error": "Request body must be a JSON object"})

    # Inject provider and model from path
    body["provider"] = provider
    body["model"] = model

    try:
        entry = PriceEntry.model_validate(body)
    except ValidationError as e:
        return _build_response(400, {"error": f"Validation failed: {e.error_count()} errors", "details": e.errors()})

    try:
        _put_price_item(entry)
    except Exception:
        logger.exception("Failed to store price: %s/%s", provider, model)
        return _build_response(500, {"error": "Failed to store price"})

    logger.info("Upserted pricing entry: %s/%s", provider, model)
    return _build_response(
        200,
        {
            "message": f"Price upserted: {provider}/{model}",
            "price": entry.model_dump(),
        },
    )


def _handle_delete_price(provider: str, model: str) -> dict[str, Any]:
    """DELETE /pricing/{provider}/{model} -- delete a pricing override."""
    try:
        deleted = _delete_price_item(provider, model)
    except Exception:
        logger.exception("Failed to delete price: %s/%s", provider, model)
        return _build_response(500, {"error": "Failed to delete price"})

    if not deleted:
        return _build_response(404, {"error": f"Price override not found: {provider}/{model}"})

    # Check if a static fallback exists
    has_static = (provider, model) in PRICING_TABLE
    logger.info("Deleted pricing override: %s/%s (static fallback: %s)", provider, model, has_static)
    return _build_response(
        200,
        {
            "message": f"Price override deleted: {provider}/{model}",
            "static_fallback": has_static,
        },
    )


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
    """Lambda Function URL handler for pricing admin CRUD.

    Routes:
        GET    /pricing                      -- list all prices (DDB + static merged)
        GET    /pricing/{provider}/{model}   -- get a specific price
        PUT    /pricing/{provider}/{model}   -- upsert a pricing entry
        DELETE /pricing/{provider}/{model}   -- delete a pricing override
    """
    path, method = _extract_path_and_method(event)
    provider, model = _extract_provider_model(path)

    if method == "GET" and provider is None:
        return _handle_list_prices()

    if method == "GET" and provider is not None and model is not None:
        return _handle_get_price(provider, model)

    if method == "PUT" and provider is not None and model is not None:
        return _handle_upsert_price(provider, model, event)

    if method == "DELETE" and provider is not None and model is not None:
        return _handle_delete_price(provider, model)

    return _build_response(405, {"error": f"Method not allowed: {method} {path}"})
