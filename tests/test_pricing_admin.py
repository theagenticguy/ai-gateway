"""Tests for the Pricing Admin REST API.

Covers all CRUD routes: list, get, upsert, delete.
Tests DDB-first lookups, static fallback, merged listing,
validation errors, 404/405, and DDB failure resilience.
Uses mocked DynamoDB via unittest.mock.
"""

from __future__ import annotations

import base64
import json
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError

from pricing_admin.handler import handler
from pricing_admin.models import PriceEntry, PriceSummary

# -- Helpers ------------------------------------------------------------------


def _make_event(
    method: str = "GET",
    path: str = "/pricing",
    body: dict[str, Any] | str | None = None,
    is_base64: bool = False,
) -> dict[str, Any]:
    """Build a Lambda Function URL event."""
    event: dict[str, Any] = {
        "requestContext": {"http": {"method": method, "path": path}},
        "isBase64Encoded": is_base64,
    }
    if body is not None:
        event["body"] = json.dumps(body) if isinstance(body, dict) else body
    else:
        event["body"] = ""
    return event


def _parse_body(response: dict[str, Any]) -> dict[str, Any]:
    """Parse the JSON body from a Lambda response."""
    return json.loads(response["body"])


# -- Model tests --------------------------------------------------------------


class TestModels:
    def test_price_entry_minimal(self) -> None:
        entry = PriceEntry(
            provider="anthropic",
            model="claude-sonnet-4",
            input_per_1k=0.003,
            output_per_1k=0.015,
        )
        assert entry.provider == "anthropic"
        assert entry.model == "claude-sonnet-4"
        assert entry.cache_read_per_1k is None
        assert entry.cache_write_per_1k is None
        assert entry.updated_at == ""

    def test_price_entry_with_cache(self) -> None:
        entry = PriceEntry(
            provider="anthropic",
            model="claude-sonnet-4",
            input_per_1k=0.003,
            output_per_1k=0.015,
            cache_read_per_1k=0.0003,
            cache_write_per_1k=0.00375,
        )
        assert entry.cache_read_per_1k == 0.0003
        assert entry.cache_write_per_1k == 0.00375

    def test_price_entry_rejects_negative_input(self) -> None:
        with pytest.raises(ValidationError):
            PriceEntry(
                provider="anthropic",
                model="test",
                input_per_1k=-0.001,
                output_per_1k=0.015,
            )

    def test_price_entry_rejects_negative_output(self) -> None:
        with pytest.raises(ValidationError):
            PriceEntry(
                provider="anthropic",
                model="test",
                input_per_1k=0.003,
                output_per_1k=-0.015,
            )

    def test_price_summary_defaults(self) -> None:
        summary = PriceSummary(
            provider="anthropic",
            model="claude-sonnet-4",
            input_per_1k=0.003,
            output_per_1k=0.015,
        )
        assert summary.source == "static"

    def test_price_summary_dynamodb_source(self) -> None:
        summary = PriceSummary(
            provider="anthropic",
            model="claude-sonnet-4",
            input_per_1k=0.003,
            output_per_1k=0.015,
            source="dynamodb",
        )
        assert summary.source == "dynamodb"


# -- GET /pricing (list) ------------------------------------------------------


class TestListPrices:
    @patch("pricing_admin.handler.dynamodb")
    def test_list_returns_merged_ddb_and_static(self, mock_ddb: Any) -> None:
        """DDB entries take priority; static entries fill in the rest."""
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.scan.return_value = {
            "Items": [
                {
                    "PK": "PRICE#anthropic#claude-sonnet-4",
                    "SK": "CONFIG",
                    "provider": "anthropic",
                    "model": "claude-sonnet-4",
                    "input_per_1k": Decimal("0.005"),
                    "output_per_1k": Decimal("0.025"),
                },
            ]
        }

        event = _make_event(method="GET", path="/pricing")
        result = handler(event)
        assert result["statusCode"] == 200

        body = _parse_body(result)
        assert "prices" in body
        assert "total" in body
        assert body["total"] == len(body["prices"])

        # The DDB entry for anthropic/claude-sonnet-4 should be source=dynamodb
        ddb_entries = [p for p in body["prices"] if p["source"] == "dynamodb"]
        assert len(ddb_entries) == 1
        assert ddb_entries[0]["provider"] == "anthropic"
        assert ddb_entries[0]["model"] == "claude-sonnet-4"
        assert ddb_entries[0]["input_per_1k"] == 0.005

        # The static anthropic/claude-sonnet-4 should NOT appear (overridden)
        static_anthropic_sonnet = [
            p
            for p in body["prices"]
            if p["source"] == "static" and p["provider"] == "anthropic" and p["model"] == "claude-sonnet-4"
        ]
        assert len(static_anthropic_sonnet) == 0

        # Static entries for other models should still be present
        static_entries = [p for p in body["prices"] if p["source"] == "static"]
        assert len(static_entries) > 0

    @patch("pricing_admin.handler.dynamodb")
    def test_list_no_ddb_entries(self, mock_ddb: Any) -> None:
        """When DDB has no entries, all static entries are returned."""
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.scan.return_value = {"Items": []}

        event = _make_event(method="GET", path="/pricing")
        result = handler(event)
        assert result["statusCode"] == 200

        body = _parse_body(result)
        # All entries should be static
        for price in body["prices"]:
            assert price["source"] == "static"
        assert body["total"] > 0

    @patch("pricing_admin.handler.dynamodb")
    def test_list_ddb_error_returns_static_only(self, mock_ddb: Any) -> None:
        """If DDB scan fails, static entries are still returned."""
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.scan.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "DDB down"}},
            "Scan",
        )

        event = _make_event(method="GET", path="/pricing")
        result = handler(event)
        assert result["statusCode"] == 200

        body = _parse_body(result)
        assert body["total"] > 0
        for price in body["prices"]:
            assert price["source"] == "static"


# -- GET /pricing/{provider}/{model} ------------------------------------------


class TestGetPrice:
    @patch("pricing_admin.handler.dynamodb")
    def test_get_returns_ddb_entry_when_exists(self, mock_ddb: Any) -> None:
        """DDB entry takes priority over static."""
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.get_item.return_value = {
            "Item": {
                "PK": "PRICE#anthropic#claude-sonnet-4",
                "SK": "CONFIG",
                "provider": "anthropic",
                "model": "claude-sonnet-4",
                "input_per_1k": Decimal("0.005"),
                "output_per_1k": Decimal("0.025"),
                "updated_at": "2026-03-26T00:00:00+00:00",
            }
        }

        event = _make_event(method="GET", path="/pricing/anthropic/claude-sonnet-4")
        result = handler(event)
        assert result["statusCode"] == 200

        body = _parse_body(result)
        assert body["source"] == "dynamodb"
        assert body["price"]["provider"] == "anthropic"
        assert body["price"]["model"] == "claude-sonnet-4"
        assert body["price"]["input_per_1k"] == 0.005
        assert body["price"]["output_per_1k"] == 0.025

    @patch("pricing_admin.handler.dynamodb")
    def test_get_falls_back_to_static(self, mock_ddb: Any) -> None:
        """When DDB has no entry, return the static price."""
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.get_item.return_value = {}  # No Item key

        event = _make_event(method="GET", path="/pricing/anthropic/claude-sonnet-4")
        result = handler(event)
        assert result["statusCode"] == 200

        body = _parse_body(result)
        assert body["source"] == "static"
        assert body["price"]["provider"] == "anthropic"
        assert body["price"]["model"] == "claude-sonnet-4"
        assert body["price"]["input_per_1k"] == 0.003
        assert body["price"]["output_per_1k"] == 0.015

    @patch("pricing_admin.handler.dynamodb")
    def test_get_unknown_provider_model_returns_404(self, mock_ddb: Any) -> None:
        """Neither DDB nor static has the entry."""
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.get_item.return_value = {}

        event = _make_event(method="GET", path="/pricing/unknown/unknown")
        result = handler(event)
        assert result["statusCode"] == 404

        body = _parse_body(result)
        assert "not found" in body["error"].lower()

    @patch("pricing_admin.handler.dynamodb")
    def test_get_ddb_error_returns_500(self, mock_ddb: Any) -> None:
        """DDB error on get returns 500 (not a silent fallback)."""
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.get_item.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "DDB down"}},
            "GetItem",
        )

        event = _make_event(method="GET", path="/pricing/anthropic/claude-sonnet-4")
        result = handler(event)
        assert result["statusCode"] == 500

        body = _parse_body(result)
        assert "error" in body

    @patch("pricing_admin.handler.dynamodb")
    def test_get_with_cache_fields(self, mock_ddb: Any) -> None:
        """DDB entry with cache pricing fields is returned correctly."""
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.get_item.return_value = {
            "Item": {
                "PK": "PRICE#anthropic#claude-sonnet-4",
                "SK": "CONFIG",
                "provider": "anthropic",
                "model": "claude-sonnet-4",
                "input_per_1k": Decimal("0.003"),
                "output_per_1k": Decimal("0.015"),
                "cache_read_per_1k": Decimal("0.0003"),
                "cache_write_per_1k": Decimal("0.00375"),
                "updated_at": "2026-03-26T00:00:00+00:00",
            }
        }

        event = _make_event(method="GET", path="/pricing/anthropic/claude-sonnet-4")
        result = handler(event)
        assert result["statusCode"] == 200

        body = _parse_body(result)
        assert body["price"]["cache_read_per_1k"] == 0.0003
        assert body["price"]["cache_write_per_1k"] == 0.00375


# -- PUT /pricing/{provider}/{model} ------------------------------------------


class TestUpsertPrice:
    @patch("pricing_admin.handler.dynamodb")
    def test_upsert_valid_entry(self, mock_ddb: Any) -> None:
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.put_item.return_value = {}

        event = _make_event(
            method="PUT",
            path="/pricing/anthropic/claude-sonnet-4",
            body={"input_per_1k": 0.003, "output_per_1k": 0.015},
        )
        result = handler(event)
        assert result["statusCode"] == 200

        body = _parse_body(result)
        assert "upserted" in body["message"].lower()
        assert body["price"]["provider"] == "anthropic"
        assert body["price"]["model"] == "claude-sonnet-4"
        assert body["price"]["input_per_1k"] == 0.003
        assert body["price"]["output_per_1k"] == 0.015

        # Verify DDB put was called
        mock_table.put_item.assert_called_once()

    @patch("pricing_admin.handler.dynamodb")
    def test_upsert_with_cache_fields(self, mock_ddb: Any) -> None:
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.put_item.return_value = {}

        event = _make_event(
            method="PUT",
            path="/pricing/anthropic/claude-sonnet-4",
            body={
                "input_per_1k": 0.003,
                "output_per_1k": 0.015,
                "cache_read_per_1k": 0.0003,
                "cache_write_per_1k": 0.00375,
            },
        )
        result = handler(event)
        assert result["statusCode"] == 200

        body = _parse_body(result)
        assert body["price"]["cache_read_per_1k"] == 0.0003
        assert body["price"]["cache_write_per_1k"] == 0.00375

        # Verify both cache fields are in the DDB item
        put_kwargs = mock_table.put_item.call_args[1]
        item = put_kwargs["Item"]
        assert "cache_read_per_1k" in item
        assert "cache_write_per_1k" in item

    def test_upsert_invalid_json_body(self) -> None:
        event = _make_event(
            method="PUT",
            path="/pricing/anthropic/claude-sonnet-4",
            body="not valid json!!!",
        )
        result = handler(event)
        assert result["statusCode"] == 400

        body = _parse_body(result)
        assert "invalid json" in body["error"].lower()

    def test_upsert_validation_error_negative_price(self) -> None:
        event = _make_event(
            method="PUT",
            path="/pricing/anthropic/claude-sonnet-4",
            body={"input_per_1k": -0.001, "output_per_1k": 0.015},
        )
        result = handler(event)
        assert result["statusCode"] == 400

        body = _parse_body(result)
        assert "validation" in body["error"].lower()

    def test_upsert_missing_required_fields(self) -> None:
        event = _make_event(
            method="PUT",
            path="/pricing/anthropic/claude-sonnet-4",
            body={"input_per_1k": 0.003},  # missing output_per_1k
        )
        result = handler(event)
        assert result["statusCode"] == 400

        body = _parse_body(result)
        assert "validation" in body["error"].lower()

    def test_upsert_body_not_object(self) -> None:
        """Body is valid JSON but not an object (e.g. a list)."""
        event = _make_event(
            method="PUT",
            path="/pricing/anthropic/claude-sonnet-4",
        )
        event["body"] = json.dumps([1, 2, 3])
        result = handler(event)
        assert result["statusCode"] == 400

        body = _parse_body(result)
        assert "json object" in body["error"].lower()

    @patch("pricing_admin.handler.dynamodb")
    def test_upsert_ddb_error_returns_500(self, mock_ddb: Any) -> None:
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "DDB down"}},
            "PutItem",
        )

        event = _make_event(
            method="PUT",
            path="/pricing/anthropic/claude-sonnet-4",
            body={"input_per_1k": 0.003, "output_per_1k": 0.015},
        )
        result = handler(event)
        assert result["statusCode"] == 500

        body = _parse_body(result)
        assert "error" in body

    @patch("pricing_admin.handler.dynamodb")
    def test_upsert_provider_model_from_path(self, mock_ddb: Any) -> None:
        """Provider and model come from the URL path, not the body."""
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.put_item.return_value = {}

        event = _make_event(
            method="PUT",
            path="/pricing/bedrock/amazon.nova-pro-v1:0",
            body={
                "provider": "should-be-overridden",
                "model": "should-be-overridden",
                "input_per_1k": 0.0008,
                "output_per_1k": 0.0032,
            },
        )
        result = handler(event)
        assert result["statusCode"] == 200

        body = _parse_body(result)
        assert body["price"]["provider"] == "bedrock"
        assert body["price"]["model"] == "amazon.nova-pro-v1:0"


# -- DELETE /pricing/{provider}/{model} ---------------------------------------


class TestDeletePrice:
    @patch("pricing_admin.handler.dynamodb")
    def test_delete_existing_override(self, mock_ddb: Any) -> None:
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.delete_item.return_value = {}

        event = _make_event(method="DELETE", path="/pricing/anthropic/claude-sonnet-4")
        result = handler(event)
        assert result["statusCode"] == 200

        body = _parse_body(result)
        assert "deleted" in body["message"].lower()
        # anthropic/claude-sonnet-4 exists in static table
        assert body["static_fallback"] is True

    @patch("pricing_admin.handler.dynamodb")
    def test_delete_nonexistent_override_returns_404(self, mock_ddb: Any) -> None:
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.delete_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "not found"}},
            "DeleteItem",
        )

        event = _make_event(method="DELETE", path="/pricing/unknown/unknown")
        result = handler(event)
        assert result["statusCode"] == 404

        body = _parse_body(result)
        assert "not found" in body["error"].lower()

    @patch("pricing_admin.handler.dynamodb")
    def test_delete_ddb_error_returns_500(self, mock_ddb: Any) -> None:
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.delete_item.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "DDB down"}},
            "DeleteItem",
        )

        event = _make_event(method="DELETE", path="/pricing/anthropic/claude-sonnet-4")
        result = handler(event)
        assert result["statusCode"] == 500

        body = _parse_body(result)
        assert "error" in body

    @patch("pricing_admin.handler.dynamodb")
    def test_delete_no_static_fallback(self, mock_ddb: Any) -> None:
        """Deleting an override for a model not in static table."""
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.delete_item.return_value = {}

        event = _make_event(method="DELETE", path="/pricing/custom/custom-model")
        result = handler(event)
        assert result["statusCode"] == 200

        body = _parse_body(result)
        assert body["static_fallback"] is False


# -- Unsupported method -------------------------------------------------------


class TestUnsupportedMethod:
    def test_post_returns_405(self) -> None:
        event = _make_event(method="POST", path="/pricing/anthropic/claude-sonnet-4")
        result = handler(event)
        assert result["statusCode"] == 405

        body = _parse_body(result)
        assert "not allowed" in body["error"].lower()

    def test_patch_returns_405(self) -> None:
        event = _make_event(method="PATCH", path="/pricing/anthropic/claude-sonnet-4")
        result = handler(event)
        assert result["statusCode"] == 405

    def test_put_without_provider_model_returns_405(self) -> None:
        """PUT /pricing (no provider/model) is not a valid route."""
        event = _make_event(method="PUT", path="/pricing")
        result = handler(event)
        assert result["statusCode"] == 405

    def test_delete_without_provider_model_returns_405(self) -> None:
        """DELETE /pricing (no provider/model) is not a valid route."""
        event = _make_event(method="DELETE", path="/pricing")
        result = handler(event)
        assert result["statusCode"] == 405


# -- Response format -----------------------------------------------------------


class TestResponseFormat:
    @patch("pricing_admin.handler.dynamodb")
    def test_response_has_correct_headers(self, mock_ddb: Any) -> None:
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.scan.return_value = {"Items": []}

        event = _make_event(method="GET", path="/pricing")
        result = handler(event)
        assert result["headers"]["Content-Type"] == "application/json"

    @patch("pricing_admin.handler.dynamodb")
    def test_response_body_is_valid_json(self, mock_ddb: Any) -> None:
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.scan.return_value = {"Items": []}

        event = _make_event(method="GET", path="/pricing")
        result = handler(event)
        parsed = json.loads(result["body"])
        assert isinstance(parsed, dict)


# -- Path extraction edge cases -----------------------------------------------


class TestPathExtraction:
    @patch("pricing_admin.handler.dynamodb")
    def test_trailing_slash_on_list(self, mock_ddb: Any) -> None:
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.scan.return_value = {"Items": []}

        event = _make_event(method="GET", path="/pricing/")
        result = handler(event)
        # /pricing/ with no provider/model should be treated as list
        assert result["statusCode"] == 200

    @patch("pricing_admin.handler.dynamodb")
    def test_path_with_only_provider(self, mock_ddb: Any) -> None:
        """Path /pricing/anthropic (no model) should be 405 for non-GET."""
        event = _make_event(method="PUT", path="/pricing/anthropic")
        result = handler(event)
        assert result["statusCode"] == 405


# -- Base64 encoded body ------------------------------------------------------


class TestBase64Body:
    @patch("pricing_admin.handler.dynamodb")
    def test_upsert_with_base64_body(self, mock_ddb: Any) -> None:
        mock_table = MagicMock()
        mock_ddb.Table.return_value = mock_table
        mock_table.put_item.return_value = {}

        raw = json.dumps({"input_per_1k": 0.003, "output_per_1k": 0.015})
        encoded = base64.b64encode(raw.encode()).decode()

        event: dict[str, Any] = {
            "requestContext": {"http": {"method": "PUT", "path": "/pricing/anthropic/claude-sonnet-4"}},
            "body": encoded,
            "isBase64Encoded": True,
        }
        result = handler(event)
        assert result["statusCode"] == 200

        body = _parse_body(result)
        assert body["price"]["input_per_1k"] == 0.003
