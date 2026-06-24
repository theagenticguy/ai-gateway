"""Lambda handler for monthly chargeback report generation.

Triggered by Step Functions on the 1st of each month. Queries DynamoDB
usage and budget tables, generates an HTML report, and uploads to S3.

Step-Functions-invoked, not HTTP / Portkey: no request authorization, and the
report lands in S3 rather than the audit pipeline. Migrated onto gwcore
(ADR-016) for the lightest touch — structured JSON logging plus operational EMF
metrics for the report-generation outcome (success / failures by stage).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from pydantic import ValidationError

from chargeback_report.models import ReportData, ReportRequest, ReportResponse, TeamUsageSummary
from chargeback_report.report_template import render_html
from gwcore.logging import get_logger
from gwcore.telemetry import Timer, emit_metric

logger = get_logger("chargeback_report")

USAGE_TABLE = os.environ.get("USAGE_TABLE", "gateway-usage")
BUDGETS_TABLE = os.environ.get("BUDGETS_TABLE", "gateway-budgets")
REPORT_BUCKET = os.environ.get("REPORT_BUCKET", "ai-gateway-chargeback-reports")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
s3 = boto3.client("s3", region_name=AWS_REGION)


# -- DynamoDB queries ----------------------------------------------------------


def _query_team_usage(month: str) -> list[dict[str, Any]]:
    """Query all team usage records for a given month from the usage table.

    Scans for items where pk starts with USAGE#TEAM# and sk matches the
    target period. Uses the period-index GSI for efficient lookups.
    """
    table = dynamodb.Table(USAGE_TABLE)
    period_sk = f"PERIOD#{month}"

    items: list[dict[str, Any]] = []
    try:
        # Scan for team usage records matching the period
        # Using scan with filter since we need all teams for a given period
        response = table.scan(
            FilterExpression=Key("sk").eq(period_sk) & Key("pk").begins_with("USAGE#TEAM#"),
        )
        items.extend(response.get("Items", []))

        while "LastEvaluatedKey" in response:
            response = table.scan(
                FilterExpression=Key("sk").eq(period_sk) & Key("pk").begins_with("USAGE#TEAM#"),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items.extend(response.get("Items", []))
    except Exception:
        logger.exception("Failed to query usage table for month %s", month)
        raise

    logger.info("Found %d team usage records for %s", len(items), month)
    return items


def _query_budget_limits() -> dict[str, Decimal]:
    """Query budget limits for all teams.

    Returns a mapping of team name to monthly budget limit in USD.
    """
    table = dynamodb.Table(BUDGETS_TABLE)
    limits: dict[str, Decimal] = {}

    try:
        response = table.scan(
            FilterExpression=Key("scope").eq("team"),
        )
        for item in response.get("Items", []):
            team = item.get("scope_id", item.get("team", ""))
            budget = item.get("monthly_budget_usd", item.get("monthly_usd"))
            if team and budget is not None:
                limits[team] = Decimal(str(budget))

        while "LastEvaluatedKey" in response:
            response = table.scan(
                FilterExpression=Key("scope").eq("team"),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            for item in response.get("Items", []):
                team = item.get("scope_id", item.get("team", ""))
                budget = item.get("monthly_budget_usd", item.get("monthly_usd"))
                if team and budget is not None:
                    limits[team] = Decimal(str(budget))
    except Exception:
        logger.warning("Failed to query budget limits — proceeding without budget data", exc_info=True)

    return limits


def _query_previous_month_total(month: str) -> Decimal | None:
    """Query total cost from the previous month for MoM comparison."""
    year, mo = int(month[:4]), int(month[5:7])
    prev_month = f"{year - 1}-12" if mo == 1 else f"{year}-{mo - 1:02d}"

    try:
        items = _query_team_usage(prev_month)
    except Exception:
        logger.warning("Failed to query previous month %s for MoM comparison", prev_month, exc_info=True)
        return None
    else:
        if not items:
            return None
        return sum((Decimal(str(item.get("total_cost_usd", 0))) for item in items), Decimal(0))


# -- Report building ----------------------------------------------------------


def _build_team_summaries(
    usage_items: list[dict[str, Any]],
    budget_limits: dict[str, Decimal],
) -> list[TeamUsageSummary]:
    """Build per-team usage summaries from raw DynamoDB items."""
    summaries: list[TeamUsageSummary] = []

    for item in usage_items:
        pk: str = item.get("pk", "")
        team = pk.removeprefix("USAGE#TEAM#")
        if not team:
            continue

        total_cost = Decimal(str(item.get("total_cost_usd", 0)))
        budget_limit = budget_limits.get(team)

        budget_pct: Decimal | None = None
        if budget_limit is not None and budget_limit > 0:
            budget_pct = (total_cost / budget_limit) * 100

        summaries.append(
            TeamUsageSummary(
                team=team,
                total_cost_usd=total_cost,
                input_tokens=int(item.get("input_tokens", 0)),
                output_tokens=int(item.get("output_tokens", 0)),
                cached_tokens=int(item.get("cached_tokens", 0)),
                total_tokens=int(item.get("total_tokens", 0)),
                request_count=int(item.get("request_count", 0)),
                budget_limit_usd=budget_limit,
                budget_utilization_pct=budget_pct,
                top_model=str(item.get("top_model", "N/A")),
                top_model_cost_usd=Decimal(str(item.get("top_model_cost_usd", 0))),
            )
        )

    return summaries


def _build_report(request: ReportRequest) -> ReportData:
    """Build the full report data from DynamoDB queries."""
    usage_items = _query_team_usage(request.month)
    budget_limits = _query_budget_limits()
    previous_total = _query_previous_month_total(request.month)
    team_summaries = _build_team_summaries(usage_items, budget_limits)

    total_cost = sum((t.total_cost_usd for t in team_summaries), Decimal(0))
    total_tokens = sum(t.total_tokens for t in team_summaries)
    total_requests = sum(t.request_count for t in team_summaries)

    return ReportData(
        month=request.month,
        generated_at=datetime.now(tz=UTC),
        teams=team_summaries,
        total_cost_usd=total_cost,
        total_tokens=total_tokens,
        total_requests=total_requests,
        team_count=len(team_summaries),
        previous_month_cost_usd=previous_total,
    )


# -- S3 upload ----------------------------------------------------------------


def _upload_report(report_html: str, month: str) -> str:
    """Upload HTML report to S3 and return the S3 URL."""
    key = f"reports/{month}/chargeback-report-{month}.html"

    s3.put_object(
        Bucket=REPORT_BUCKET,
        Key=key,
        Body=report_html.encode("utf-8"),
        ContentType="text/html",
        ServerSideEncryption="aws:kms",
    )

    s3_url = f"s3://{REPORT_BUCKET}/{key}"
    logger.info("Uploaded report to %s", s3_url)
    return s3_url


# -- Lambda entry point -------------------------------------------------------


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    """Lambda handler for chargeback report generation.

    Input:  {"month": "2026-03", "output_format": "html"}
    Output: {"s3_url": "s3://...", "summary": "...", "team_count": N, "total_cost_usd": "X.XX", "month": "2026-03"}
    """
    with Timer("RequestLatency", route="chargeback_report"):
        return _handle(event)


def _handle(event: dict[str, Any]) -> dict[str, Any]:
    try:
        request = ReportRequest.model_validate(event)
    except ValidationError as e:
        # Surface only the error count, never the exception text — a pydantic
        # repr can echo input values.
        count = e.error_count()
        logger.exception("Invalid report request")
        emit_metric("ChargebackError", 1, dimensions={"Code": "bad_request"})
        return {"statusCode": 400, "error": f"Invalid request: {count} validation error(s)"}

    logger.info("Generating chargeback report for month=%s", request.month)

    try:
        report_data = _build_report(request)
    except Exception:
        logger.exception("Failed to build report for %s", request.month)
        emit_metric("ChargebackError", 1, dimensions={"Code": "build_error"})
        return {"statusCode": 500, "error": f"Failed to build report for {request.month}"}

    if not report_data.teams:
        logger.warning("No team usage data found for %s", request.month)

    try:
        report_html = render_html(report_data)
    except Exception:
        logger.exception("Failed to render HTML report")
        emit_metric("ChargebackError", 1, dimensions={"Code": "render_error"})
        return {"statusCode": 500, "error": "Failed to render HTML report"}

    try:
        s3_url = _upload_report(report_html, request.month)
    except Exception:
        logger.exception("Failed to upload report to S3")
        emit_metric("ChargebackError", 1, dimensions={"Code": "upload_error"})
        return {"statusCode": 500, "error": "Failed to upload report to S3"}

    summary = (
        f"Chargeback report for {request.month}: "
        f"{report_data.team_count} teams, "
        f"${report_data.total_cost_usd:,.2f} total cost, "
        f"{report_data.total_tokens:,} tokens across {report_data.total_requests:,} requests"
    )

    response = ReportResponse(
        s3_url=s3_url,
        summary=summary,
        team_count=report_data.team_count,
        total_cost_usd=report_data.total_cost_usd,
        month=request.month,
    )

    emit_metric("ReportGenerated", 1, dimensions={"Route": "chargeback_report"})
    logger.info("Report generated: %s", summary)
    return response.model_dump(mode="json")
