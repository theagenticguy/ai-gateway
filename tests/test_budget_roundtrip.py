"""Round-trip regression test for issue #261.

Admin-created budgets were invisible to enforcement because the two services
used different DynamoDB key schemas against the same ``gateway-budgets`` table:
budget_admin writes ``budget_id``/``scope``/``scope_id``/``scope_type`` (matching
the physical table + ``scope-index`` GSI), while enforcement read a nonexistent
``pk``/``sk`` key, which raises ValidationException against real DynamoDB and
silently failed open (``budget-check-degraded``).

This test stands up a REAL moto-backed DynamoDB table with the ACTUAL key
schema from ``infrastructure/modules/budgets/main.tf`` (hash=budget_id,
range=scope, plus the ``scope-index`` GSI HASH=scope RANGE=scope_id), writes a
budget through the budget_admin ``create_budget`` path, then asserts enforcement
can see and enforce it. Using the real key schema is the crux: it is what makes
the pre-fix ``get_item(Key={"pk": ..., "sk": ...})`` blow up.

``TestBudgetRoundTrip`` mocks the usage-table reads to stay scoped to the
budgets-table key mismatch. ``TestBudgetUsageEndToEnd`` closes the loop: it
stands up BOTH physical tables (budgets + usage) at their real schemas, writes
usage through cost_attribution's real ``_accumulate_usage`` path, and reads it
back through enforcement's real ``_get_current_usage`` with NO usage mock —
proving the writer and reader agree on the ``gateway-usage`` schema too.
"""

from __future__ import annotations

import base64
import json
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from budget_admin import routes as admin_routes
from budget_enforcement import handler as enforcement
from budget_enforcement.models import BudgetCheckRequest
from cost_attribution import handler as cost_attribution
from cost_attribution.models import MetricResult
from gwcore import auth
from rate_limiter import handler as rate_limiter

TABLE_NAME = "gateway-budgets"
USAGE_TABLE_NAME = "gateway-usage"
REGION = "us-east-1"


def _make_jwt(claims: dict[str, Any]) -> str:
    """Build a fake (unverified) JWT with the given payload claims."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(b"fakesig").decode().rstrip("=")
    return f"{header}.{payload}.{signature}"


def _create_budgets_table(dynamodb: Any) -> Any:
    """Create the ``gateway-budgets`` table with the real Terraform key schema."""
    return dynamodb.create_table(
        TableName=TABLE_NAME,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[
            {"AttributeName": "budget_id", "KeyType": "HASH"},
            {"AttributeName": "scope", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "budget_id", "AttributeType": "S"},
            {"AttributeName": "scope", "AttributeType": "S"},
            {"AttributeName": "scope_id", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "scope-index",
                "KeySchema": [
                    {"AttributeName": "scope", "KeyType": "HASH"},
                    {"AttributeName": "scope_id", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )


def _create_usage_table(dynamodb: Any) -> Any:
    """Create the ``gateway-usage`` table with the real Terraform key schema.

    hash=``scope_id``, range=``period_date``, plus the ``period-index`` GSI
    (HASH=``period_date``) and a TTL attribute ``expires_at`` — matching
    infrastructure/modules/budgets/main.tf.
    """
    return dynamodb.create_table(
        TableName=USAGE_TABLE_NAME,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[
            {"AttributeName": "scope_id", "KeyType": "HASH"},
            {"AttributeName": "period_date", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "scope_id", "AttributeType": "S"},
            {"AttributeName": "period_date", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "period-index",
                "KeySchema": [{"AttributeName": "period_date", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )


def _create_budget_via_admin(team: str, budget_usd: str) -> str:
    """Exercise the budget_admin write path and return the new budget_id."""
    principal = auth.Principal(sub="admin-user", scopes=frozenset({auth.INVOKE_SCOPE}), team="admin-team")
    body = {
        "scope": "team",
        "scope_id": team,
        "budget_usd": budget_usd,
        "period": "monthly",
        "tier": "standard",
        "alert_thresholds": [50, 80, 100],
        "model_limits": [{"model": "claude-opus-4", "max_cost_usd": "200"}],
    }
    event = {
        "requestContext": {"requestId": "rid-test", "http": {"method": "POST", "path": "/budgets"}},
        "body": json.dumps(body),
        "isBase64Encoded": False,
    }
    with patch("budget_admin.routes.audit.emit"):
        result = admin_routes.create_budget(event, principal)
    return result["body"] if isinstance(result["body"], str) else json.loads(result["body"])["budget_id"]


@pytest.fixture
def moto_budgets_table(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Stand up a real moto DynamoDB budgets table wired into both services."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        _create_budgets_table(dynamodb)

        # Point budget_admin's write path at the moto table.
        admin_routes.init_dynamodb(TABLE_NAME, "gateway-usage", region=REGION)
        # Point budget_enforcement's read path at the moto-backed resource + table.
        monkeypatch.setattr(enforcement, "dynamodb", boto3.resource("dynamodb", region_name=REGION))
        monkeypatch.setattr(enforcement, "BUDGETS_TABLE", TABLE_NAME)
        yield dynamodb


@pytest.fixture
def moto_both_tables(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Stand up BOTH real-schema moto tables wired into every service.

    This is the full end-to-end path (issue #261): usage is written by
    cost_attribution's real ``_accumulate_usage`` and read by budget_enforcement
    with NO usage mock, proving admin budgets and accumulated usage line up on
    the physical schemas.
    """
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        _create_budgets_table(dynamodb)
        _create_usage_table(dynamodb)

        resource = boto3.resource("dynamodb", region_name=REGION)

        # budget_admin write path -> moto tables.
        admin_routes.init_dynamodb(TABLE_NAME, USAGE_TABLE_NAME, region=REGION)
        # Point every service's module-level resource + table names at moto.
        for module in (enforcement, cost_attribution, rate_limiter):
            monkeypatch.setattr(module, "dynamodb", resource, raising=False)
        monkeypatch.setattr(enforcement, "BUDGETS_TABLE", TABLE_NAME)
        monkeypatch.setattr(enforcement, "USAGE_TABLE", USAGE_TABLE_NAME)
        monkeypatch.setattr(cost_attribution, "BUDGETS_TABLE", TABLE_NAME)
        monkeypatch.setattr(cost_attribution, "USAGE_TABLE", USAGE_TABLE_NAME)
        monkeypatch.setattr(rate_limiter, "USAGE_TABLE", USAGE_TABLE_NAME)
        yield dynamodb


def _accumulate_team_spend(team: str, cost_usd: float) -> None:
    """Write team usage through cost_attribution's REAL accumulation path."""
    metric = MetricResult(
        provider="anthropic",
        model="claude-opus-4",
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
        cost_usd=cost_usd,
        team=team,
        user="user1",
    )
    cost_attribution._accumulate_usage([metric])


class TestBudgetUsageEndToEnd:
    """Full round-trip over BOTH physical tables with NO usage mock (issue #261).

    Usage is written via cost_attribution's real ``_accumulate_usage`` and read
    back by budget_enforcement's real ``_get_current_usage`` — the proof that
    the writer and reader agree on the physical ``gateway-usage`` schema.
    """

    def test_denies_when_accumulated_usage_over_admin_budget(self, moto_both_tables: Any) -> None:
        # Admin sets a low $100 budget; real usage of $150 is written.
        _create_budget_via_admin(team="platform", budget_usd="100")
        _accumulate_team_spend("platform", 150.0)

        # Sanity: enforcement reads the accumulated spend WITHOUT any mock.
        assert enforcement._get_current_usage("platform") == Decimal(150)

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        result = enforcement._check_budget(BudgetCheckRequest(jwt_token=jwt))

        assert result.allowed is False
        assert result.status_code == 429
        assert "exceeded" in result.reason.lower()
        assert result.budget_status is not None
        assert result.budget_status.monthly_budget_usd == Decimal(100)
        assert result.budget_status.current_spend_usd == Decimal(150)

    def test_allows_when_accumulated_usage_under_admin_budget(self, moto_both_tables: Any) -> None:
        _create_budget_via_admin(team="platform", budget_usd="5000")
        _accumulate_team_spend("platform", 100.0)

        assert enforcement._get_current_usage("platform") == Decimal(100)

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        result = enforcement._check_budget(BudgetCheckRequest(jwt_token=jwt))

        assert result.allowed is True
        assert result.budget_status is not None
        assert result.budget_status.monthly_budget_usd == Decimal(5000)
        assert result.budget_status.current_spend_usd == Decimal(100)

    def test_no_usage_written_reads_zero_and_allows(self, moto_both_tables: Any) -> None:
        # A budget exists but no usage has been accumulated: real read is 0.
        _create_budget_via_admin(team="platform", budget_usd="5000")

        assert enforcement._get_current_usage("platform") == Decimal("0.00")

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        result = enforcement._check_budget(BudgetCheckRequest(jwt_token=jwt))

        assert result.allowed is True


class TestBudgetRoundTrip:
    def test_admin_budget_visible_to_enforcement(self, moto_budgets_table: Any) -> None:
        """A budget created via budget_admin is found by enforcement's lookup."""
        _create_budget_via_admin(team="platform", budget_usd="5000")

        record = enforcement._get_budget_record("platform")

        assert record is not None, "enforcement could not see the admin-created budget"
        assert record["scope_type"] == "team"
        assert record["scope_id"] == "platform"
        # Field-vocabulary translation applied.
        assert Decimal(str(record["monthly_budget_usd"])) == Decimal(5000)
        assert record["warn_threshold_pct"] == 80.0  # highest alert threshold < 100
        assert record["hard_limit_pct"] == 100

    def test_no_budget_for_unknown_team_returns_none(self, moto_budgets_table: Any) -> None:
        """A team with no configured budget returns None (falls back to tiers)."""
        _create_budget_via_admin(team="platform", budget_usd="5000")
        assert enforcement._get_budget_record("no-such-team") is None

    def test_user_scoped_budget_not_matched_as_team(self, moto_budgets_table: Any) -> None:
        """A user-scoped budget sharing the id is not returned for the team lookup."""
        principal = auth.Principal(sub="admin", scopes=frozenset({auth.INVOKE_SCOPE}), team="admin")
        body = {"scope": "user", "scope_id": "platform", "budget_usd": "10"}
        event = {
            "requestContext": {"requestId": "r", "http": {"method": "POST", "path": "/budgets"}},
            "body": json.dumps(body),
            "isBase64Encoded": False,
        }
        with patch("budget_admin.routes.audit.emit"):
            admin_routes.create_budget(event, principal)

        assert enforcement._get_budget_record("platform") is None

    def test_enforcement_blocks_when_usage_over_admin_budget(self, moto_budgets_table: Any) -> None:
        """Full round-trip: admin sets a low budget, usage above it -> deny (429)."""
        _create_budget_via_admin(team="platform", budget_usd="100")

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        req = BudgetCheckRequest(jwt_token=jwt)

        # Mock only the usage-table reads (separate, out-of-scope key mismatch).
        with patch.object(enforcement, "_get_current_usage", return_value=Decimal("150.00")):
            result = enforcement._check_budget(req)

        assert result.allowed is False
        assert result.status_code == 429
        assert "exceeded" in result.reason.lower()
        assert result.budget_status is not None
        assert result.budget_status.monthly_budget_usd == Decimal(100)

    def test_enforcement_allows_when_usage_under_admin_budget(self, moto_budgets_table: Any) -> None:
        """Full round-trip: usage below the admin budget -> allow."""
        _create_budget_via_admin(team="platform", budget_usd="5000")

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        req = BudgetCheckRequest(jwt_token=jwt)

        with patch.object(enforcement, "_get_current_usage", return_value=Decimal("100.00")):
            result = enforcement._check_budget(req)

        assert result.allowed is True
        assert result.budget_status is not None
        assert result.budget_status.monthly_budget_usd == Decimal(5000)

    def test_admin_model_limit_enforced(self, moto_budgets_table: Any) -> None:
        """The admin list-form model_limits round-trips into a per-model cap."""
        _create_budget_via_admin(team="platform", budget_usd="5000")

        jwt = _make_jwt({"custom:team": "platform", "sub": "user1"})
        req = BudgetCheckRequest(jwt_token=jwt, model="claude-opus-4")

        with (
            patch.object(enforcement, "_get_current_usage", return_value=Decimal("100.00")),
            patch.object(enforcement, "_get_model_usage", return_value=Decimal("250.00")),
        ):
            result = enforcement._check_budget(req)

        assert result.allowed is False
        assert result.status_code == 429
        assert result.error is not None
        assert result.error.model == "claude-opus-4"
        assert result.error.limit_usd == Decimal(200)
