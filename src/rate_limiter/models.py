"""Pydantic v2 models for rate limiting."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RateLimitResult(BaseModel):
    """Result of a rate limit check against RPM and daily token limits."""

    allowed: bool = Field(description="Whether the request is allowed")
    reason: str = Field(default="", description="Explanation when request is denied")
    retry_after_seconds: int | None = Field(
        default=None,
        description="Seconds until the rate limit window resets (included when blocked)",
    )
    current_rpm: int = Field(default=0, description="Current requests in the active minute window")
    current_daily_tokens: int = Field(default=0, description="Current token count for the day")
