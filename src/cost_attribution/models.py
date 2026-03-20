"""Pydantic v2 models for cost attribution input/output validation."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class UsageMetrics(BaseModel):
    """Token usage from a gateway log record."""

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def coerce_token_values(cls, data: dict) -> dict:
        """Coerce non-int values (strings, None) to int 0."""
        if not isinstance(data, dict):
            return data
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            val = data.get(field)
            if val is None:
                data[field] = 0
            elif not isinstance(val, int):
                try:
                    data[field] = int(val)
                except (ValueError, TypeError):
                    data[field] = 0
        return data

    @model_validator(mode="after")
    def compute_total(self) -> UsageMetrics:
        """Fill total_tokens from prompt + completion if zero."""
        if self.total_tokens == 0:
            self.total_tokens = self.prompt_tokens + self.completion_tokens
        return self

    @property
    def has_tokens(self) -> bool:
        return self.total_tokens > 0


class RequestHeaders(BaseModel):
    """Relevant headers from the gateway request."""

    x_portkey_provider: str = Field(default="", alias="x-portkey-provider")

    model_config = {"populate_by_name": True}


class RequestInfo(BaseModel):
    """Nested request object from log record."""

    headers: RequestHeaders = Field(default_factory=RequestHeaders)


class LogRecord(BaseModel):
    """A parsed gateway log record containing usage and routing info."""

    usage: UsageMetrics | None = None
    model: str = Field(default="unknown")
    provider: str = Field(default="")
    req: RequestInfo = Field(default_factory=RequestInfo)

    @property
    def resolved_provider(self) -> str:
        """Resolve provider from header, then field, then 'unknown'."""
        header_provider = self.req.headers.x_portkey_provider
        if header_provider:
            return header_provider
        return self.provider or "unknown"


class MetricResult(BaseModel):
    """Extracted metric data ready for CloudWatch publishing."""

    provider: str
    model: str
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)


class HandlerResponse(BaseModel):
    """Lambda handler response."""

    statusCode: int  # noqa: N815
    total_events: int = 0
    processed: int = 0
    skipped: int = 0
    errors: int = 0
    error: str | None = None
