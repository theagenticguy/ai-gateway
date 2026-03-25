"""Prompt injection detection patterns.

DESIGN PRINCIPLE: Low false-positive rate on coding content.

Coding agents routinely send prompts that contain instructions, system-like
text, code snippets, and natural-language phrases that *look* like injection
but are perfectly benign.  Every pattern here is tuned to avoid triggering on:

- "Write a function to ignore empty strings"
- "Create a system prompt for a chatbot"
- "You are a helpful coding assistant" (inside a system message field)
- Code comments containing "ignore", "override", "system", etc.

Only flag content that is *unambiguously* attempting to hijack the model's
behaviour (e.g. literal "ignore all previous instructions" as a standalone
directive, or raw chat-ML delimiters).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from content_scanner.models import InjectionDetection, ScanMode, ScanResult, Severity


@dataclass(frozen=True, slots=True)
class InjectionPattern:
    """A compiled injection detection pattern."""

    name: str
    regex: re.Pattern[str]
    severity: Severity


# ---------------------------------------------------------------------------
# Pattern definitions — compiled once at module load
# ---------------------------------------------------------------------------
#
# Each regex uses word boundaries and/or negative lookaheads to avoid matching
# normal coding/instructional content.
#

_PATTERNS: tuple[InjectionPattern, ...] = (
    # ── instruction_override ──────────────────────────────────────────────
    # Matches explicit directives to discard prior instructions.
    # Requires the verb ("ignore", "disregard", "forget", "override") to be
    # immediately followed by words like "previous/prior/above/all/system
    # instructions/prompt/rules".
    # Will NOT match: "ignore empty strings", "ignore this error", etc.
    InjectionPattern(
        name="instruction_override",
        regex=re.compile(
            r"""(?ix)                         # case-insensitive, verbose
            (?:^|\.\s+|\n\s*)                 # start of text, sentence, or line
            (?:please\s+)?                    # optional politeness
            (?:ignore|disregard|forget|override|bypass|skip|do\s+not\s+follow)
            \s+
            (?:all\s+)?
            (?:previous|prior|above|earlier|existing|original|old|preceding|initial|system)
            \s+
            (?:instructions?|prompts?|rules?|guidelines?|directives?|context|constraints?)
            """,
            re.MULTILINE,
        ),
        severity=Severity.critical,
    ),
    # ── role_hijack ───────────────────────────────────────────────────────
    # Matches attempts to redefine the model's identity/role at runtime.
    # Requires "you are now" or "act as" followed by identity-like phrases.
    # Will NOT match: "you are a helpful assistant" in a normal system prompt
    # design context (we require "now" or imperative framing).
    InjectionPattern(
        name="role_hijack",
        regex=re.compile(
            r"""(?ix)
            (?:^|\.\s+|\n\s*)
            (?:
                you\s+are\s+now\s+            # "you are now a ..."
              | from\s+now\s+on,?\s+you\s+are # "from now on you are ..."
              | switch\s+(?:to|into)\s+(?:being\s+)?  # "switch to being ..."
              | pretend\s+(?:to\s+be|you(?:'re|\s+are))\s+  # "pretend to be ..."
              | i\s+want\s+you\s+to\s+act\s+as\s+(?!a\s+(?:function|method|variable|class|module)\b)
                                              # "I want you to act as" but not
                                              # "act as a function" (coding)
            )
            """,
            re.MULTILINE,
        ),
        severity=Severity.high,
    ),
    # ── system_prompt_extraction ──────────────────────────────────────────
    # Matches attempts to exfiltrate the system prompt.
    # Will NOT match: "Create a system prompt for ..." (design task).
    InjectionPattern(
        name="system_prompt_extraction",
        regex=re.compile(
            r"""(?ix)
            (?:
                (?:show|reveal|display|print|output|repeat|echo|leak|dump|give|tell)
                \s+(?:me\s+)?
                (?:your|the|my|this)?\s*
                (?:full\s+|complete\s+|entire\s+|original\s+|exact\s+)?
                (?:system\s+(?:prompt|message|instructions?)|initial\s+(?:prompt|instructions?))
              |
                what\s+(?:is|are)\s+your\s+system\s+(?:prompt|instructions?)
            )
            """,
            re.MULTILINE,
        ),
        severity=Severity.high,
    ),
    # ── delimiter_injection ───────────────────────────────────────────────
    # Matches raw chat-ML / internal delimiters injected into user content.
    # These are never legitimate in user-facing content.
    InjectionPattern(
        name="delimiter_injection",
        regex=re.compile(
            r"""(?x)
            <\|im_start\|>
          | <\|im_end\|>
          | <\|system\|>
          | <\|user\|>
          | <\|assistant\|>
          | \[INST\]\s*<<SYS>>
          | <<SYS>>.*?<</SYS>>
          | \[/INST\]
            """,
            re.DOTALL,
        ),
        severity=Severity.critical,
    ),
    # ── encoded_payload ───────────────────────────────────────────────────
    # Matches base64 or eval() in a suspicious instructional context.
    # Requires explicit instruction framing: "decode and execute", "eval(",
    # "base64 decode the following and run it".
    # Will NOT match: normal base64 strings in code, API tokens, etc.
    InjectionPattern(
        name="encoded_payload",
        regex=re.compile(
            r"""(?ix)
            (?:
                (?:decode|decrypt)\s+(?:and\s+)?(?:execute|run|follow|eval)
              | (?:execute|run|follow|eval)\s+(?:the\s+)?(?:decoded|decrypted|base64)
              | eval\s*\(\s*(?:atob|base64\.b64decode|Buffer\.from)
            )
            """,
        ),
        severity=Severity.medium,
    ),
)


def scan_injection(text: str, mode: ScanMode = ScanMode.detect) -> ScanResult:
    """Scan *text* for prompt injection patterns.

    Parameters
    ----------
    text:
        The content to scan.
    mode:
        ``off`` skips scanning; ``detect`` / ``redact`` / ``block`` all
        return detections (injection content is never "redacted" — it is
        either allowed or blocked).

    Returns
    -------
    ScanResult
        With ``injection_detections`` populated.
    """
    if mode == ScanMode.off:
        return ScanResult()

    detections: list[InjectionDetection] = []

    for pattern in _PATTERNS:
        match = pattern.regex.search(text)
        if match:
            detections.append(
                InjectionDetection(
                    pattern_name=pattern.name,
                    severity=pattern.severity,
                    matched_text=match.group(0).strip()[:200],  # cap for safety
                )
            )

    return ScanResult(
        detected=len(detections) > 0,
        injection_detections=detections,
    )


def get_patterns() -> tuple[InjectionPattern, ...]:
    """Return compiled patterns (useful for testing / introspection)."""
    return _PATTERNS
