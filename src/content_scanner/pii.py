"""PII detection and redaction via Amazon Comprehend."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

import boto3

from content_scanner.models import PiiDetection, ScanMode, ScanResult

logger = logging.getLogger("content_scanner.pii")

# Comprehend enforces a 100 KB (UTF-8 bytes) limit per DetectPiiEntities call.
_COMPREHEND_BYTE_LIMIT = 100_000

# PII entity types we care about — acts as an allow-list filter on Comprehend output.
SUPPORTED_PII_TYPES: frozenset[str] = frozenset(
    {
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
    }
)

_comprehend_client = None


def _get_comprehend_client():
    """Lazy-initialise the Comprehend client (cold-start friendly)."""
    global _comprehend_client  # noqa: PLW0603
    if _comprehend_client is None:
        _comprehend_client = boto3.client("comprehend")
    return _comprehend_client


def _chunk_text(text: str, max_bytes: int = _COMPREHEND_BYTE_LIMIT) -> list[tuple[int, str]]:
    """Split *text* into chunks that each fit within *max_bytes* UTF-8.

    Returns a list of ``(byte_offset, chunk)`` tuples so callers can adjust
    character offsets back to the original string.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return [(0, text)]

    # UTF-8 continuation byte markers
    _utf8_continuation_mask = 0xC0
    _utf8_continuation_value = 0x80

    chunks: list[tuple[int, str]] = []
    start = 0
    while start < len(encoded):
        end = min(start + max_bytes, len(encoded))
        # Avoid splitting inside a multi-byte character.
        while end > start and (encoded[end - 1] & _utf8_continuation_mask) == _utf8_continuation_value:
            end -= 1
        chunk_bytes = encoded[start:end]
        chunks.append((start, chunk_bytes.decode("utf-8", errors="replace")))
        start = end
    return chunks


def _call_comprehend(text: str) -> list[dict[str, Any]]:
    """Call Comprehend DetectPiiEntities for a single text chunk."""
    client = _get_comprehend_client()
    response = client.detect_pii_entities(Text=text, LanguageCode="en")
    return response.get("Entities", [])


def _build_redaction_token(entity_type: str, counter: Counter[str]) -> str:
    """Generate a numbered redaction token like ``[EMAIL_1]``."""
    counter[entity_type] += 1
    return f"[{entity_type}_{counter[entity_type]}]"


def scan_pii(
    text: str,
    mode: ScanMode = ScanMode.detect,
    *,
    allowed_types: frozenset[str] | None = None,
    score_threshold: float = 0.7,
) -> ScanResult:
    """Scan *text* for PII using Amazon Comprehend.

    Parameters
    ----------
    text:
        The content to scan.
    mode:
        ``detect`` returns detections only; ``redact`` also replaces PII with
        type-specific tokens (``[EMAIL_1]``, ``[SSN_1]``, etc.).
    allowed_types:
        Subset of ``SUPPORTED_PII_TYPES`` to look for. ``None`` means all.
    score_threshold:
        Minimum confidence to treat a detection as real.

    Returns
    -------
    ScanResult
        With ``pii_detections`` populated and optionally ``redacted_content``.
    """
    if mode == ScanMode.off:
        return ScanResult()

    target_types = (allowed_types or SUPPORTED_PII_TYPES) & SUPPORTED_PII_TYPES

    detections: list[PiiDetection] = []
    chunks = _chunk_text(text)

    for byte_offset, chunk in chunks:
        raw_entities = _call_comprehend(chunk)
        # Comprehend returns character offsets relative to the chunk.
        # We need to translate them to offsets in the original *text*.
        # byte_offset is in bytes; for the character offset we compute from the
        # original text by counting how many characters precede the chunk.
        char_offset = len(text.encode("utf-8")[:byte_offset].decode("utf-8", errors="replace"))

        for entity in raw_entities:
            etype = entity.get("Type", "")
            score = entity.get("Score", 0.0)
            begin = entity.get("BeginOffset", 0)
            end = entity.get("EndOffset", 0)

            if etype not in target_types:
                continue
            if score < score_threshold:
                continue

            detections.append(
                PiiDetection(
                    entity_type=etype,
                    score=score,
                    begin_offset=char_offset + begin,
                    end_offset=char_offset + end,
                )
            )

    result = ScanResult(detected=len(detections) > 0, pii_detections=detections)

    if mode == ScanMode.redact and detections:
        result.redacted_content = _redact(text, detections)

    return result


def _redact(text: str, detections: list[PiiDetection]) -> str:
    """Replace PII spans with numbered tokens.

    Processes detections from end to start so earlier offsets stay valid.
    Also mutates each detection's ``.token`` field in-place with the
    replacement string.
    """
    counter: Counter[str] = Counter()
    # Sort by begin_offset descending so replacements don't shift earlier offsets.
    sorted_dets = sorted(detections, key=lambda d: d.begin_offset, reverse=True)
    result = text
    # First pass: assign tokens (we need ascending order for numbering).
    for det in sorted(sorted_dets, key=lambda d: d.begin_offset):
        det.token = _build_redaction_token(det.entity_type, counter)
    # Second pass: replace spans from end to start.
    for det in sorted_dets:
        result = result[: det.begin_offset] + det.token + result[det.end_offset :]
    return result
