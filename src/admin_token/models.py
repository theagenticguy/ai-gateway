"""Pydantic v2 models for the token-exchange API."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Audience(StrEnum):
    """Audiences a gateway token can be bound to."""

    CLAUDE = "claude"
    CODEX = "codex"
    GENERIC = "generic"


class TokenExchangeRequest(BaseModel):
    """POST /auth/token request body.

    The caller presents an already-authenticated session (verified upstream);
    this body only declares the desired audience and optional TTL clamp.
    """

    audience: Audience = Field(default=Audience.GENERIC, description="Target CLI/client audience")
    ttl_seconds: int = Field(
        default=3600, ge=300, le=43200, description="Requested token lifetime (clamped server-side)"
    )


class TokenExchangeResponse(BaseModel):
    """Minted gateway access token."""

    access_token: str
    token_type: str = "Bearer"  # noqa: S105 — OAuth token_type label, not a credential
    expires_in: int
    audience: str
    team: str
    scope: str
