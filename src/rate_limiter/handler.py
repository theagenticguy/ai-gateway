"""Rate limiting enforcement using DynamoDB atomic counters.

Provides RPM (requests-per-minute) and daily token limit checks.
Uses the same usage table as budget enforcement with different PK prefixes.

Graceful degradation: if DynamoDB is unreachable the request is allowed
and a warning is logged.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

import boto3
from botocore.exceptions import ClientError

from rate_limiter.models import RateLimitResult

logger = logging.getLogger("rate_limiter")
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

USAGE_TABLE = os.environ.get("USAGE_TABLE", "gateway-usage")

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def _get_table():
    """Return the DynamoDB usage table resource."""
    return dynamodb.Table(USAGE_TABLE)


def _increment_rpm_counter(team: str) -> int:
    """Atomically increment the RPM counter for the current minute bucket.

    Key schema:
        PK = RATE#RPM#{team}
        SK = MINUTE#{epoch_minute}

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
            "pk": f"RATE#RPM#{team}",
            "sk": f"MINUTE#{minute_bucket}",
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

    Key schema:
        PK = RATE#TOKENS#{team}
        SK = DAY#{YYYY-MM-DD}

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
            "pk": f"RATE#TOKENS#{team}",
            "sk": f"DAY#{day_str}",
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
    - Key: PK=RATE#RPM#{team}, SK=MINUTE#{minute_bucket}
    - Atomic ADD 1 on each call
    - TTL: expires_at = current minute epoch + 120 seconds
    - Compare count against rpm_limit

    Daily token check:
    - Key: PK=RATE#TOKENS#{team}, SK=DAY#{YYYY-MM-DD}
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
            return RateLimitResult(allowed=True, reason="rate-limit-degraded")

        if current_rpm > rpm_limit:
            logger.info(
                "RPM limit exceeded for team=%s (%d > %d)",
                team,
                current_rpm,
                rpm_limit,
            )
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
            logger.warning(
                "DynamoDB error during daily token check (team=%s), allowing request",
                team,
                exc_info=True,
            )
            return RateLimitResult(
                allowed=True,
                reason="rate-limit-degraded",
                current_rpm=current_rpm,
            )

        if current_daily_tokens > tokens_per_day_limit:
            logger.info(
                "Daily token limit exceeded for team=%s (%d > %d)",
                team,
                current_daily_tokens,
                tokens_per_day_limit,
            )
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
