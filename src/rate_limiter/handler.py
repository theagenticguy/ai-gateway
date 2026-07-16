"""Rate limiting enforcement using DynamoDB atomic counters.

Provides RPM (requests-per-minute) and daily token limit checks via the
``check_rate_limit`` library function. This is a pure module — it has no Lambda
handler, no request event, and performs no authorization; ``budget_enforcement``
(the pre-request webhook) imports and calls it on the hot path, and that caller
emits the deny-audit. So this module emits metrics and structured logs only,
never an audit event (which would double-count the same denial).

Uses the same usage table as budget enforcement with different PK prefixes.

Graceful degradation: if DynamoDB is unreachable the request is allowed and a
warning is logged — a rate-limit-store outage must never block traffic.

Migrated onto gwcore (ADR-016): structured JSON logging + denial/degraded
metrics. The ``check_rate_limit`` contract and degradation behavior are
unchanged.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import boto3
from botocore.exceptions import ClientError

from gwcore.logging import get_logger
from gwcore.telemetry import emit_metric
from rate_limiter.models import RateLimitResult

logger = get_logger("rate_limiter")

USAGE_TABLE = os.environ.get("USAGE_TABLE", "gateway-usage")

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def _get_table():
    """Return the DynamoDB usage table resource."""
    return dynamodb.Table(USAGE_TABLE)


def _increment_rpm_counter(team: str) -> int:
    """Atomically increment the RPM counter for the current minute bucket.

    Key schema (real ``gateway-usage`` table, issue #261 — hash=``scope_id``,
    range=``period_date``, see infrastructure/modules/budgets/main.tf):
        scope_id    = ratelimit#rpm#{team}
        period_date = minute#{epoch_minute}

    The distinct ``ratelimit#`` scope_id prefix keeps these counters from
    colliding with the monthly spend rows (``team#…`` / ``user#…``).

    TTL: expires_at = start-of-current-minute epoch + 120 seconds (two full
    minutes), giving DynamoDB time to clean up after the window closes.

    Returns the updated request count for this minute.
    """
    now = datetime.now(tz=UTC)
    epoch_seconds = int(now.timestamp())
    minute_bucket = epoch_seconds // 60
    expires_at = (minute_bucket * 60) + 120  # two minutes from bucket start

    table = _get_table()
    resp = table.update_item(
        Key={
            "scope_id": f"ratelimit#rpm#{team}",
            "period_date": f"minute#{minute_bucket}",
        },
        UpdateExpression="SET #cnt = if_not_exists(#cnt, :zero) + :inc, #ttl = :ttl",
        ExpressionAttributeNames={
            "#cnt": "request_count",
            "#ttl": "expires_at",
        },
        ExpressionAttributeValues={
            ":zero": 0,
            ":inc": 1,
            ":ttl": expires_at,
        },
        ReturnValues="UPDATED_NEW",
    )
    return int(resp["Attributes"]["request_count"])


def _increment_daily_token_counter(team: str, tokens: int) -> int:
    """Atomically add tokens to the daily counter for a team.

    Key schema (real ``gateway-usage`` table, issue #261 — hash=``scope_id``,
    range=``period_date``):
        scope_id    = ratelimit#tokens#{team}
        period_date = day#{YYYY-MM-DD}

    TTL: expires_at = end-of-day epoch + 3600 seconds (one hour grace).

    Returns the updated token count for today.
    """
    now = datetime.now(tz=UTC)
    day_str = now.strftime("%Y-%m-%d")

    # End of day in epoch seconds + 1 hour grace
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
    expires_at = int(end_of_day.timestamp()) + 3600

    table = _get_table()
    resp = table.update_item(
        Key={
            "scope_id": f"ratelimit#tokens#{team}",
            "period_date": f"day#{day_str}",
        },
        UpdateExpression="SET #cnt = if_not_exists(#cnt, :zero) + :inc, #ttl = :ttl",
        ExpressionAttributeNames={
            "#cnt": "token_count",
            "#ttl": "expires_at",
        },
        ExpressionAttributeValues={
            ":zero": 0,
            ":inc": tokens,
            ":ttl": expires_at,
        },
        ReturnValues="UPDATED_NEW",
    )
    return int(resp["Attributes"]["token_count"])


def _seconds_until_next_minute() -> int:
    """Seconds remaining until the next minute boundary."""
    now = datetime.now(tz=UTC)
    return max(1, 60 - now.second)


def _seconds_until_end_of_day() -> int:
    """Seconds remaining until midnight UTC."""
    now = datetime.now(tz=UTC)
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
    delta = end_of_day - now
    return max(1, int(delta.total_seconds()))


def check_rate_limit(
    team: str,
    rpm_limit: int,
    tokens_per_day_limit: int,
    estimated_tokens: int = 0,
) -> RateLimitResult:
    """Check RPM and daily token limits for a team.

    RPM check: DynamoDB sliding window counter
    - Key: scope_id=ratelimit#rpm#{team}, period_date=minute#{minute_bucket}
    - Atomic ADD 1 on each call
    - TTL: expires_at = current minute epoch + 120 seconds
    - Compare count against rpm_limit

    Daily token check:
    - Key: scope_id=ratelimit#tokens#{team}, period_date=day#{YYYY-MM-DD}
    - Atomic ADD estimated_tokens on each call
    - TTL: expires_at = end of day + 3600 seconds
    - Compare against tokens_per_day_limit (-1 = unlimited)

    Graceful degradation: if DDB errors, return allowed=True with warning logged.
    """
    current_rpm = 0
    current_daily_tokens = 0

    # ── RPM check ────────────────────────────────────────────────────────
    if rpm_limit > 0:
        try:
            current_rpm = _increment_rpm_counter(team)
        except (ClientError, Exception):
            logger.warning(
                "DynamoDB error during RPM check (team=%s), allowing request",
                team,
                exc_info=True,
            )
            emit_metric("RateLimitDegraded", 1, dimensions={"Check": "rpm"})
            return RateLimitResult(allowed=True, reason="rate-limit-degraded")

        if current_rpm > rpm_limit:
            logger.info(
                "RPM limit exceeded for team=%s (%d > %d)",
                team,
                current_rpm,
                rpm_limit,
            )
            emit_metric("RateLimitDenied", 1, dimensions={"Check": "rpm"})
            return RateLimitResult(
                allowed=False,
                reason=f"RPM limit exceeded ({current_rpm}/{rpm_limit} requests per minute)",
                retry_after_seconds=_seconds_until_next_minute(),
                current_rpm=current_rpm,
                current_daily_tokens=current_daily_tokens,
            )

    # ── Daily token check ────────────────────────────────────────────────
    if tokens_per_day_limit != -1 and estimated_tokens > 0:
        try:
            current_daily_tokens = _increment_daily_token_counter(team, estimated_tokens)
        except (ClientError, Exception):
            logger.warning(  # nosemgrep: python-logger-credential-disclosure
                "DynamoDB error during daily token check (team=%s), allowing request",
                team,
                exc_info=True,
            )
            emit_metric("RateLimitDegraded", 1, dimensions={"Check": "daily_tokens"})
            return RateLimitResult(
                allowed=True,
                reason="rate-limit-degraded",
                current_rpm=current_rpm,
            )

        if current_daily_tokens > tokens_per_day_limit:
            logger.info(  # nosemgrep: python-logger-credential-disclosure
                "Daily token limit exceeded for team=%s (%d > %d)",
                team,
                current_daily_tokens,
                tokens_per_day_limit,
            )
            emit_metric("RateLimitDenied", 1, dimensions={"Check": "daily_tokens"})
            return RateLimitResult(
                allowed=False,
                reason=f"Daily token limit exceeded ({current_daily_tokens:,}/{tokens_per_day_limit:,} tokens per day)",
                retry_after_seconds=_seconds_until_end_of_day(),
                current_rpm=current_rpm,
                current_daily_tokens=current_daily_tokens,
            )

    return RateLimitResult(
        allowed=True,
        current_rpm=current_rpm,
        current_daily_tokens=current_daily_tokens,
    )
