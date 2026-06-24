"""Tests for the monthly chargeback report generator.

Covers report generation with mock DynamoDB data, HTML rendering,
empty months, budget utilization, and S3 upload.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chargeback_report.handler import (
    _build_team_summaries,
    _upload_report,
    handler,
)
from chargeback_report.models import (
    ReportData,
    ReportRequest,
    ReportResponse,
    TeamUsageSummary,
)
from chargeback_report.report_template import (
    _budget_color,
    _format_pct,
    _format_tokens,
    _format_usd,
    render_html,
)

# -- Fixtures -----------------------------------------------------------------


TEAM_ALPHA_USAGE = {
    "pk": "USAGE#TEAM#alpha",
    "sk": "PERIOD#2026-03",
    "total_tokens": 500000,
    "input_tokens": 300000,
    "output_tokens": 200000,
    "cached_tokens": 50000,
    "total_cost_usd": Decimal("125.50"),
    "request_count": 1200,
    "top_model": "claude-sonnet-4",
    "top_model_cost_usd": Decimal("95.00"),
}

TEAM_BETA_USAGE = {
    "pk": "USAGE#TEAM#beta",
    "sk": "PERIOD#2026-03",
    "total_tokens": 1200000,
    "input_tokens": 800000,
    "output_tokens": 400000,
    "cached_tokens": 120000,
    "total_cost_usd": Decimal("340.75"),
    "request_count": 3500,
    "top_model": "gpt-4.1",
    "top_model_cost_usd": Decimal("280.00"),
}

TEAM_GAMMA_USAGE = {
    "pk": "USAGE#TEAM#gamma",
    "sk": "PERIOD#2026-03",
    "total_tokens": 50000,
    "input_tokens": 30000,
    "output_tokens": 20000,
    "cached_tokens": 0,
    "total_cost_usd": Decimal("12.30"),
    "request_count": 85,
    "top_model": "gpt-4.1-mini",
    "top_model_cost_usd": Decimal("10.00"),
}

ALL_USAGE_ITEMS = [TEAM_ALPHA_USAGE, TEAM_BETA_USAGE, TEAM_GAMMA_USAGE]

BUDGET_LIMITS = {
    "alpha": Decimal("200.00"),
    "beta": Decimal("500.00"),
    "gamma": Decimal("50.00"),
}


def _make_report_data(
    teams: list[TeamUsageSummary] | None = None,
    previous_month_cost: Decimal | None = None,
) -> ReportData:
    """Build a ReportData fixture."""
    if teams is None:
        teams = _build_team_summaries(ALL_USAGE_ITEMS, BUDGET_LIMITS)
    total_cost = sum(t.total_cost_usd for t in teams)
    total_tokens = sum(t.total_tokens for t in teams)
    total_requests = sum(t.request_count for t in teams)
    return ReportData(
        month="2026-03",
        generated_at=datetime(2026, 4, 1, 6, 0, 0, tzinfo=UTC),
        teams=teams,
        total_cost_usd=total_cost,
        total_tokens=total_tokens,
        total_requests=total_requests,
        team_count=len(teams),
        previous_month_cost_usd=previous_month_cost,
    )


# =============================================================================
# Models
# =============================================================================


class TestReportRequest:
    def test_valid_request(self) -> None:
        req = ReportRequest(month="2026-03", output_format="html")
        assert req.month == "2026-03"
        assert req.output_format == "html"
        assert req.send_email is False

    def test_invalid_month_format(self) -> None:
        with pytest.raises(ValueError, match="month"):
            ReportRequest(month="March 2026")

    def test_invalid_month_value(self) -> None:
        with pytest.raises(ValueError, match="month"):
            ReportRequest(month="2026-13")

    def test_defaults(self) -> None:
        req = ReportRequest(month="2026-01")
        assert req.output_format == "html"
        assert req.send_email is False


class TestTeamUsageSummary:
    def test_over_budget(self) -> None:
        summary = TeamUsageSummary(
            team="alpha",
            total_cost_usd=Decimal("150.00"),
            budget_utilization_pct=Decimal("150.0"),
        )
        assert summary.is_over_budget

    def test_under_budget(self) -> None:
        summary = TeamUsageSummary(
            team="alpha",
            total_cost_usd=Decimal("50.00"),
            budget_utilization_pct=Decimal("50.0"),
        )
        assert not summary.is_over_budget

    def test_no_budget_not_over(self) -> None:
        summary = TeamUsageSummary(team="alpha", total_cost_usd=Decimal("50.00"))
        assert not summary.is_over_budget


class TestReportData:
    def test_month_over_month_increase(self) -> None:
        report = _make_report_data(previous_month_cost=Decimal("400.00"))
        pct = report.month_over_month_change_pct
        assert pct is not None
        assert pct > 0  # cost went from 400 to ~478

    def test_month_over_month_decrease(self) -> None:
        report = _make_report_data(previous_month_cost=Decimal("600.00"))
        pct = report.month_over_month_change_pct
        assert pct is not None
        assert pct < 0  # cost went from 600 to ~478

    def test_month_over_month_no_previous(self) -> None:
        report = _make_report_data(previous_month_cost=None)
        assert report.month_over_month_change_pct is None

    def test_month_over_month_zero_previous(self) -> None:
        report = _make_report_data(previous_month_cost=Decimal(0))
        assert report.month_over_month_change_pct is None


class TestReportResponse:
    def test_serialization(self) -> None:
        resp = ReportResponse(
            s3_url="s3://bucket/key",
            summary="test summary",
            team_count=3,
            total_cost_usd=Decimal("478.55"),
            month="2026-03",
        )
        data = resp.model_dump(mode="json")
        assert data["s3_url"] == "s3://bucket/key"
        assert data["team_count"] == 3


# =============================================================================
# Team Summary Building
# =============================================================================


class TestBuildTeamSummaries:
    def test_three_teams(self) -> None:
        summaries = _build_team_summaries(ALL_USAGE_ITEMS, BUDGET_LIMITS)
        assert len(summaries) == 3
        teams = {s.team for s in summaries}
        assert teams == {"alpha", "beta", "gamma"}

    def test_budget_utilization_calculated(self) -> None:
        summaries = _build_team_summaries(ALL_USAGE_ITEMS, BUDGET_LIMITS)
        by_team = {s.team: s for s in summaries}

        # alpha: 125.50 / 200.00 = 62.75%
        assert by_team["alpha"].budget_utilization_pct is not None
        assert Decimal(62) < by_team["alpha"].budget_utilization_pct < Decimal(63)

        # beta: 340.75 / 500.00 = 68.15%
        assert by_team["beta"].budget_utilization_pct is not None
        assert Decimal(68) < by_team["beta"].budget_utilization_pct < Decimal(69)

        # gamma: 12.30 / 50.00 = 24.6%
        assert by_team["gamma"].budget_utilization_pct is not None
        assert Decimal(24) < by_team["gamma"].budget_utilization_pct < Decimal(25)

    def test_no_budget_limit(self) -> None:
        summaries = _build_team_summaries(ALL_USAGE_ITEMS, {})
        for s in summaries:
            assert s.budget_limit_usd is None
            assert s.budget_utilization_pct is None

    def test_token_counts(self) -> None:
        summaries = _build_team_summaries(ALL_USAGE_ITEMS, BUDGET_LIMITS)
        by_team = {s.team: s for s in summaries}
        assert by_team["alpha"].input_tokens == 300000
        assert by_team["alpha"].output_tokens == 200000
        assert by_team["alpha"].cached_tokens == 50000
        assert by_team["alpha"].total_tokens == 500000

    def test_empty_items(self) -> None:
        summaries = _build_team_summaries([], BUDGET_LIMITS)
        assert summaries == []

    def test_malformed_pk_skipped(self) -> None:
        items = [{"pk": "USAGE#TEAM#", "sk": "PERIOD#2026-03", "total_cost_usd": Decimal(10)}]
        summaries = _build_team_summaries(items, {})
        assert len(summaries) == 0


# =============================================================================
# HTML Rendering
# =============================================================================


class TestFormatHelpers:
    def test_format_usd(self) -> None:
        assert _format_usd(Decimal("1234.50")) == "$1,234.50"
        assert _format_usd(Decimal("0.00")) == "$0.00"

    def test_format_tokens(self) -> None:
        assert _format_tokens(1234567) == "1,234,567"
        assert _format_tokens(0) == "0"

    def test_format_pct_none(self) -> None:
        assert _format_pct(None) == "N/A"

    def test_format_pct_value(self) -> None:
        assert _format_pct(Decimal("62.75")) == "62.8%"

    def test_budget_color_green(self) -> None:
        assert _budget_color(Decimal("50.0")) == "#28a745"

    def test_budget_color_orange(self) -> None:
        assert _budget_color(Decimal("85.0")) == "#fd7e14"

    def test_budget_color_red(self) -> None:
        assert _budget_color(Decimal("105.0")) == "#dc3545"

    def test_budget_color_none(self) -> None:
        assert _budget_color(None) == "#888888"


class TestRenderHtml:
    def test_produces_valid_html(self) -> None:
        report = _make_report_data()
        html = render_html(report)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_contains_month(self) -> None:
        report = _make_report_data()
        html = render_html(report)
        assert "2026-03" in html

    def test_contains_all_teams(self) -> None:
        report = _make_report_data()
        html = render_html(report)
        assert "alpha" in html
        assert "beta" in html
        assert "gamma" in html

    def test_contains_total_cost(self) -> None:
        report = _make_report_data()
        html = render_html(report)
        assert "$478.55" in html

    def test_sorted_by_cost_descending(self) -> None:
        report = _make_report_data()
        html = render_html(report)
        # beta (340.75) should appear before alpha (125.50) before gamma (12.30)
        beta_pos = html.index("beta")
        alpha_pos = html.index("alpha")
        gamma_pos = html.index("gamma")
        assert beta_pos < alpha_pos < gamma_pos

    def test_empty_report(self) -> None:
        report = _make_report_data(teams=[])
        html = render_html(report)
        assert "<!DOCTYPE html>" in html
        assert "TOTAL" in html
        assert "$0.00" in html

    def test_mom_section_present_when_data_available(self) -> None:
        report = _make_report_data(previous_month_cost=Decimal("400.00"))
        html = render_html(report)
        assert "Month-over-Month" in html

    def test_mom_section_absent_when_no_data(self) -> None:
        report = _make_report_data(previous_month_cost=None)
        html = render_html(report)
        assert "Month-over-Month" not in html

    def test_budget_colors_in_html(self) -> None:
        report = _make_report_data()
        html = render_html(report)
        # All teams are under budget, should have green color
        assert "#28a745" in html


# =============================================================================
# S3 Upload
# =============================================================================


class TestS3Upload:
    @patch("chargeback_report.handler.s3")
    def test_upload_returns_s3_url(self, mock_s3: Any) -> None:
        url = _upload_report("<html>test</html>", "2026-03")
        assert url.startswith("s3://")
        assert "2026-03" in url
        mock_s3.put_object.assert_called_once()

    @patch("chargeback_report.handler.s3")
    def test_upload_uses_kms_encryption(self, mock_s3: Any) -> None:
        _upload_report("<html>test</html>", "2026-03")
        call_kwargs = mock_s3.put_object.call_args.kwargs
        assert call_kwargs["ServerSideEncryption"] == "aws:kms"

    @patch("chargeback_report.handler.s3")
    def test_upload_content_type(self, mock_s3: Any) -> None:
        _upload_report("<html>test</html>", "2026-03")
        call_kwargs = mock_s3.put_object.call_args.kwargs
        assert call_kwargs["ContentType"] == "text/html"

    @patch("chargeback_report.handler.s3")
    def test_upload_key_format(self, mock_s3: Any) -> None:
        _upload_report("<html>test</html>", "2026-03")
        call_kwargs = mock_s3.put_object.call_args.kwargs
        assert call_kwargs["Key"] == "reports/2026-03/chargeback-report-2026-03.html"


# =============================================================================
# Lambda Handler (end-to-end)
# =============================================================================


def _mock_scan_side_effect(items: list[dict], **_kwargs: Any) -> dict:
    """Simulate a DynamoDB scan response."""
    return {"Items": items}


class TestHandler:
    @patch("chargeback_report.handler.s3")
    @patch("chargeback_report.handler.dynamodb")
    def test_successful_report(self, mock_ddb: Any, mock_s3: Any) -> None:
        mock_table = MagicMock()
        mock_table.scan.side_effect = [
            {"Items": ALL_USAGE_ITEMS},  # usage query for current month
            {
                "Items": [  # budgets query
                    {"scope": "team", "scope_id": "alpha", "monthly_budget_usd": Decimal(200)},
                    {"scope": "team", "scope_id": "beta", "monthly_budget_usd": Decimal(500)},
                    {"scope": "team", "scope_id": "gamma", "monthly_budget_usd": Decimal(50)},
                ]
            },
            {"Items": ALL_USAGE_ITEMS},  # usage query for previous month
        ]
        mock_ddb.Table.return_value = mock_table

        result = handler({"month": "2026-03", "output_format": "html"})
        assert result["team_count"] == 3
        assert "s3_url" in result
        assert "summary" in result
        mock_s3.put_object.assert_called_once()

    @patch("chargeback_report.handler.s3")
    @patch("chargeback_report.handler.dynamodb")
    def test_empty_month(self, mock_ddb: Any, mock_s3: Any) -> None:
        mock_table = MagicMock()
        mock_table.scan.return_value = {"Items": []}
        mock_ddb.Table.return_value = mock_table

        result = handler({"month": "2026-01", "output_format": "html"})
        assert result["team_count"] == 0

    def test_invalid_request(self) -> None:
        result = handler({"month": "invalid"})
        assert result["statusCode"] == 400
        assert "error" in result

    def test_missing_month(self) -> None:
        result = handler({})
        assert result["statusCode"] == 400

    @patch("chargeback_report.handler.s3")
    @patch("chargeback_report.handler.dynamodb")
    def test_s3_failure(self, mock_ddb: Any, mock_s3: Any) -> None:
        mock_table = MagicMock()
        mock_table.scan.return_value = {"Items": ALL_USAGE_ITEMS}
        mock_ddb.Table.return_value = mock_table
        mock_s3.put_object.side_effect = Exception("S3 access denied")

        result = handler({"month": "2026-03"})
        assert result["statusCode"] == 500
        assert "S3" in result["error"]

    @patch("chargeback_report.handler.s3")
    @patch("chargeback_report.handler.dynamodb")
    def test_dynamodb_failure(self, mock_ddb: Any, mock_s3: Any) -> None:
        mock_table = MagicMock()
        mock_table.scan.side_effect = Exception("DynamoDB timeout")
        mock_ddb.Table.return_value = mock_table

        result = handler({"month": "2026-03"})
        assert result["statusCode"] == 500

    @patch("chargeback_report.handler.s3")
    @patch("chargeback_report.handler.dynamodb")
    def test_response_has_correct_month(self, mock_ddb: Any, mock_s3: Any) -> None:
        mock_table = MagicMock()
        mock_table.scan.return_value = {"Items": [TEAM_ALPHA_USAGE]}
        mock_ddb.Table.return_value = mock_table

        result = handler({"month": "2026-03"})
        assert result["month"] == "2026-03"

    @patch("chargeback_report.handler.s3")
    @patch("chargeback_report.handler.dynamodb")
    def test_total_cost_in_response(self, mock_ddb: Any, mock_s3: Any) -> None:
        mock_table = MagicMock()
        mock_table.scan.side_effect = [
            {"Items": [TEAM_ALPHA_USAGE]},
            {"Items": []},  # no budgets
            {"Items": []},  # no previous month
        ]
        mock_ddb.Table.return_value = mock_table

        result = handler({"month": "2026-03"})
        assert "total_cost_usd" in result
        # Decimal serialized as string in JSON mode
        assert float(result["total_cost_usd"]) == pytest.approx(125.50, rel=1e-2)


# -- gwcore observability (ADR-016) -------------------------------------------


class TestObservability:
    """The migration adds operational EMF metrics for the report-generation
    outcome. No audit events — the report lands in S3, not the audit pipeline."""

    @patch("chargeback_report.handler.emit_metric")
    @patch("chargeback_report.handler.s3")
    @patch("chargeback_report.handler.dynamodb")
    def test_success_emits_report_generated(self, mock_ddb: Any, mock_s3: Any, mock_metric: Any) -> None:
        mock_table = MagicMock()
        mock_table.scan.side_effect = [
            {"Items": [TEAM_ALPHA_USAGE]},
            {"Items": []},
            {"Items": []},
        ]
        mock_ddb.Table.return_value = mock_table

        result = handler({"month": "2026-03"})
        assert "s3_url" in result
        assert any(c.args and c.args[0] == "ReportGenerated" for c in mock_metric.call_args_list)

    @patch("chargeback_report.handler.emit_metric")
    def test_bad_request_emits_error_metric(self, mock_metric: Any) -> None:
        result = handler({"month": "invalid"})
        assert result["statusCode"] == 400
        assert any(
            c.args and c.args[0] == "ChargebackError" and c.kwargs.get("dimensions") == {"Code": "bad_request"}
            for c in mock_metric.call_args_list
        )

    def test_bad_request_does_not_echo_payload(self) -> None:
        # The ValidationError text must not leak input values into the response.
        secret = "super-secret-value-2026"
        result = handler({"month": secret})
        assert result["statusCode"] == 400
        assert secret not in result["error"]
        assert "validation error" in result["error"].lower()

    @patch("chargeback_report.handler.emit_metric")
    @patch("chargeback_report.handler.render_html")
    @patch("chargeback_report.handler.s3")
    @patch("chargeback_report.handler.dynamodb")
    def test_render_error_emits_metric(self, mock_ddb: Any, mock_s3: Any, mock_render: Any, mock_metric: Any) -> None:
        mock_table = MagicMock()
        mock_table.scan.return_value = {"Items": []}
        mock_ddb.Table.return_value = mock_table
        mock_render.side_effect = RuntimeError("template boom")

        result = handler({"month": "2026-03"})
        assert result["statusCode"] == 500
        assert any(
            c.args and c.args[0] == "ChargebackError" and c.kwargs.get("dimensions") == {"Code": "render_error"}
            for c in mock_metric.call_args_list
        )

    @patch("chargeback_report.handler.emit_metric")
    @patch("chargeback_report.handler.dynamodb")
    def test_build_error_emits_metric(self, mock_ddb: Any, mock_metric: Any) -> None:
        mock_table = MagicMock()
        mock_table.scan.side_effect = Exception("DynamoDB timeout")
        mock_ddb.Table.return_value = mock_table

        result = handler({"month": "2026-03"})
        assert result["statusCode"] == 500
        assert any(
            c.args and c.args[0] == "ChargebackError" and c.kwargs.get("dimensions") == {"Code": "build_error"}
            for c in mock_metric.call_args_list
        )

    @patch("chargeback_report.handler.emit_metric")
    @patch("chargeback_report.handler.s3")
    @patch("chargeback_report.handler.dynamodb")
    def test_upload_error_emits_metric(self, mock_ddb: Any, mock_s3: Any, mock_metric: Any) -> None:
        mock_table = MagicMock()
        mock_table.scan.return_value = {"Items": [TEAM_ALPHA_USAGE]}
        mock_ddb.Table.return_value = mock_table
        mock_s3.put_object.side_effect = Exception("S3 access denied")

        result = handler({"month": "2026-03"})
        assert result["statusCode"] == 500
        assert any(
            c.args and c.args[0] == "ChargebackError" and c.kwargs.get("dimensions") == {"Code": "upload_error"}
            for c in mock_metric.call_args_list
        )
