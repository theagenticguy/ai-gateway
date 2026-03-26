"""Pydantic v2 models for the content scanner Lambda."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

# ── Enums ────────────────────────────────────────────────────────────────────


class ScanMode(StrEnum):
    """How the scanner should handle detections."""

    off = "off"
    detect = "detect"
    redact = "redact"
    block = "block"


class Severity(StrEnum):
    """Severity level for injection pattern matches."""

    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


# ── Detection payloads ───────────────────────────────────────────────────────


class PiiDetection(BaseModel):
    """A single PII entity detected in the content."""

    entity_type: str
    score: float = Field(ge=0.0, le=1.0)
    begin_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    token: str = Field(default="", description="Redaction placeholder, e.g. [EMAIL_1]")


class InjectionDetection(BaseModel):
    """A single prompt injection pattern match."""

    pattern_name: str
    severity: Severity
    matched_text: str = ""


# ── Scan result (aggregated) ─────────────────────────────────────────────────


class ScanResult(BaseModel):
    """Result of a single scan pass (PII or injection)."""

    detected: bool = False
    pii_detections: list[PiiDetection] = Field(default_factory=list)
    injection_detections: list[InjectionDetection] = Field(default_factory=list)
    redacted_content: str | None = None


# ── Team configuration ───────────────────────────────────────────────────────


class TeamScanConfig(BaseModel):
    """Per-team content scanning configuration."""

    team_id: str = "default"
    pii_mode: ScanMode = ScanMode.detect
    injection_mode: ScanMode = ScanMode.detect
    pii_types_to_scan: list[str] = Field(
        default_factory=lambda: [
            "SSN",
            "CREDIT_DEBIT_NUMBER",
            "EMAIL",
            "PHONE",
            "ADDRESS",
            "NAME",
            "DATE_OF_BIRTH",
            "DRIVER_ID",
            "PASSPORT_NUMBER",
            "AWS_ACCESS_KEY",
            "AWS_SECRET_KEY",
            "IP_ADDRESS",
        ]
    )
    pii_score_threshold: float = Field(default=0.7, ge=0.0, le=1.0)


# ── Request / Response ───────────────────────────────────────────────────────


class ScanRequest(BaseModel):
    """Inbound request to the content scanner."""

    content: str
    team_id: str = "default"
    model: str = ""
    request_id: str = ""


class ScanResponse(BaseModel):
    """Outbound response from the content scanner."""

    verdict: Literal["allow", "redact", "block"]
    request_id: str = ""
    content: str = Field(default="", description="Original or redacted content")
    detections: list[PiiDetection | InjectionDetection] = Field(default_factory=list)
    error: str | None = None


# ── AppConfig ────────────────────────────────────────────────────────────────


class ScannerAppConfig(BaseModel):
    """Feature-flag payload from AppConfig for the content scanner."""

    enabled: bool = True
    team_overrides: dict[str, bool] = Field(
        default_factory=dict,
        description="Per-team enable/disable overrides (team_id -> enabled)",
    )


# ── Portkey response ─────────────────────────────────────────────────────────


class PluginHandlerResponse(BaseModel):
    """Portkey webhook plugin response envelope."""

    verdict: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
