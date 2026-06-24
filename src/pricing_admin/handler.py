"""Pricing admin Lambda — dynamic pricing overrides, migrated onto gwcore (ADR-016).

DynamoDB overrides take priority over the static ``cost_attribution.pricing``
table. Authorization is now enforced in-handler (it was not): every request
requires the admin scope, and the upsert / delete mutations emit audit events.

Routes:
    GET    /pricing                      -- list all prices (DDB + static merged)
    GET    /pricing/{provider}/{model}   -- get a specific price
    PUT    /pricing/{provider}/{model}   -- upsert a pricing entry
    DELETE /pricing/{provider}/{model}   -- delete a pricing override
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError
from pydantic import ValidationError

from cost_attribution.pricing import PRICING_TABLE
from gwcore import audit, auth, errors, ok, responses
from gwcore.logging import bind, correlation_id, get_logger
from gwcore.responses import request_body
from gwcore.telemetry import Timer, emit_metric
from pricing_admin.models import PriceEntry, PriceSummary

logger = get_logger("pricing_admin")

PRICING_TABLE_NAME = os.environ.get("PRICING_TABLE_NAME", "gateway-pricing")
dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))

# Minimum path parts for provider/model extraction: /pricing/{provider}/{model} → 3
_MIN_PATH_PARTS_WITH_PROVIDER_MODEL = 3


# -- DynamoDB helpers ----------------------------------------------------------


def _make_pk(provider: str, model: str) -> str:
    return f"PRICE#{provider}#{model}"


def _get_price_item(provider: str, model: str) -> dict[str, Any] | None:
    table = dynamodb.Table(PRICING_TABLE_NAME)
    return table.get_item(Key={"PK": _make_pk(provider, model), "SK": "CONFIG"}).get("Item")


def _list_price_items() -> list[dict[str, Any]]:
    table = dynamodb.Table(PRICING_TABLE_NAME)
    resp = table.scan(FilterExpression="SK = :sk", ExpressionAttributeValues={":sk": "CONFIG"})
    return resp.get("Items", [])


def _put_price_item(entry: PriceEntry) -> None:
    table = dynamodb.Table(PRICING_TABLE_NAME)
    item: dict[str, Any] = {
        "PK": _make_pk(entry.provider, entry.model),
        "SK": "CONFIG",
        "provider": entry.provider,
        "model": entry.model,
        "input_per_1k": Decimal(str(entry.input_per_1k)),
        "output_per_1k": Decimal(str(entry.output_per_1k)),
        "updated_at": datetime.now(tz=UTC).isoformat(),
    }
    if entry.cache_read_per_1k is not None:
        item["cache_read_per_1k"] = Decimal(str(entry.cache_read_per_1k))
    if entry.cache_write_per_1k is not None:
        item["cache_write_per_1k"] = Decimal(str(entry.cache_write_per_1k))
    table.put_item(Item=item)


def _delete_price_item(provider: str, model: str) -> bool:
    """Delete a pricing entry. Returns True if it existed."""
    table = dynamodb.Table(PRICING_TABLE_NAME)
    try:
        table.delete_item(
            Key={"PK": _make_pk(provider, model), "SK": "CONFIG"},
            ConditionExpression="attribute_exists(PK)",
        )
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


def _provider_model(path: str) -> tuple[str | None, str | None]:
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) >= _MIN_PATH_PARTS_WITH_PROVIDER_MODEL and parts[0] == "pricing":
        return parts[1], parts[2]
    return None, None


def _audit(event: dict[str, Any], principal: auth.Principal, **kw: Any) -> None:
    audit.emit(audit.event_from_request(event, actor=principal.sub, team=principal.team, **kw))


# -- Route handlers ------------------------------------------------------------


def _list_prices() -> dict[str, Any]:
    """GET /pricing — list DynamoDB overrides merged with static fallbacks."""
    summaries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    try:
        for item in _list_price_items():
            provider, model = item.get("provider", ""), item.get("model", "")
            seen.add((provider, model))
            summaries.append(
                PriceSummary(
                    provider=provider,
                    model=model,
                    input_per_1k=float(item.get("input_per_1k", 0)),
                    output_per_1k=float(item.get("output_per_1k", 0)),
                    source="dynamodb",
                ).model_dump()
            )
    except ClientError:
        logger.exception("Failed to list pricing entries from DynamoDB")

    for (provider, model), token_price in sorted(PRICING_TABLE.items()):
        if (provider, model) not in seen:
            summaries.append(
                PriceSummary(
                    provider=provider,
                    model=model,
                    input_per_1k=token_price.input_per_1k,
                    output_per_1k=token_price.output_per_1k,
                    source="static",
                ).model_dump()
            )
    return ok({"prices": summaries, "total": len(summaries)})


def _get_price(provider: str, model: str) -> dict[str, Any]:
    """GET /pricing/{provider}/{model} — DynamoDB override then static fallback."""
    try:
        item = _get_price_item(provider, model)
    except ClientError as e:
        raise errors.UpstreamError("Failed to fetch price from storage") from e

    if item:
        entry = PriceEntry(
            provider=item.get("provider", provider),
            model=item.get("model", model),
            input_per_1k=float(item.get("input_per_1k", 0)),
            output_per_1k=float(item.get("output_per_1k", 0)),
            cache_read_per_1k=(float(item["cache_read_per_1k"]) if item.get("cache_read_per_1k") is not None else None),
            cache_write_per_1k=(
                float(item["cache_write_per_1k"]) if item.get("cache_write_per_1k") is not None else None
            ),
            updated_at=item.get("updated_at", ""),
        )
        return ok({"source": "dynamodb", "price": entry.model_dump()})

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
        return ok({"source": "static", "price": entry.model_dump()})

    raise errors.NotFoundError(f"Price not found: {provider}/{model}")


def _upsert_price(provider: str, model: str, event: dict[str, Any], principal: auth.Principal) -> dict[str, Any]:
    """PUT /pricing/{provider}/{model} — upsert a pricing override."""
    import json  # noqa: PLC0415 — local parse to inject path params before validation

    try:
        body = json.loads(request_body(event))
    except (json.JSONDecodeError, ValueError) as e:
        raise errors.ValidationFailedError("Invalid JSON body") from e
    if not isinstance(body, dict):
        raise errors.ValidationFailedError("Request body must be a JSON object")

    # provider/model come from the path, not the body.
    body["provider"] = provider
    body["model"] = model
    try:
        entry = PriceEntry.model_validate(body)
    except ValidationError as e:
        raise errors.ValidationFailedError("Invalid pricing entry", details={"errors": e.errors()}) from e

    try:
        _put_price_item(entry)
    except ClientError as e:
        raise errors.UpstreamError("Failed to store price") from e

    logger.info("Upserted pricing entry: %s/%s", provider, model)
    _audit(
        event,
        principal,
        action="pricing.upsert",
        resource=f"{provider}/{model}",
        after={"input_per_1k": entry.input_per_1k, "output_per_1k": entry.output_per_1k},
    )
    return ok({"message": f"Price upserted: {provider}/{model}", "price": entry.model_dump()})


def _delete_price(provider: str, model: str, event: dict[str, Any], principal: auth.Principal) -> dict[str, Any]:
    """DELETE /pricing/{provider}/{model} — delete a pricing override."""
    try:
        deleted = _delete_price_item(provider, model)
    except ClientError as e:
        raise errors.UpstreamError("Failed to delete price") from e
    if not deleted:
        raise errors.NotFoundError(f"Price override not found: {provider}/{model}")

    has_static = (provider, model) in PRICING_TABLE
    logger.info("Deleted pricing override: %s/%s (static fallback: %s)", provider, model, has_static)
    _audit(
        event,
        principal,
        action="pricing.delete",
        resource=f"{provider}/{model}",
        detail=f"static_fallback={has_static}",
    )
    return ok({"message": f"Price override deleted: {provider}/{model}", "static_fallback": has_static})


# -- Lambda entry point --------------------------------------------------------


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:  # noqa: PLR0911 — one return per CRUD route
    """Lambda handler for pricing admin CRUD (admin scope required)."""
    cid = correlation_id(event)
    log = bind(logger, cid)
    path, method = _path_method(event)

    if path == "/health" and method == "GET":
        return ok({"status": "healthy"})

    try:
        with Timer("RequestLatency", route="pricing_admin"):
            principal = auth.build_principal(event)
            auth.require(principal, scopes=[auth.ADMIN_SCOPE])
            provider, model = _provider_model(path)

            if method == "GET" and provider is None:
                return _list_prices()
            if method == "GET" and provider and model:
                return _get_price(provider, model)
            if method == "PUT" and provider and model:
                return _upsert_price(provider, model, event, principal)
            if method == "DELETE" and provider and model:
                return _delete_price(provider, model, event, principal)
            raise errors.NotFoundError(f"Not found: {method} {path}")  # noqa: TRY301 — dispatch fallthrough
    except errors.ControlPlaneError as exc:
        if exc.status in {401, 403}:
            emit_metric("AuthzDenied", 1, dimensions={"Route": "pricing_admin"})
            try:
                actor = auth.build_principal(event).sub or "unknown"
            except errors.ControlPlaneError:
                actor = "unknown"
            audit.emit(
                audit.event_from_request(
                    event,
                    action="pricing.access",
                    actor=actor,
                    resource=f"{method} {path}",
                    decision="deny",
                    status=exc.status,
                    detail=exc.code,
                )
            )
        return responses.error_response(exc)
    except Exception:
        log.exception("Unhandled error in pricing_admin: %s %s", method, path)
        emit_metric("PricingAdminError", 1, dimensions={"Code": "internal_error"})
        return responses.error_response(errors.ControlPlaneError("Internal error"))
