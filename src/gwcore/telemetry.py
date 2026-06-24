"""CloudWatch EMF metrics + OTEL GenAI-convention span attributes.

Metrics are emitted as CloudWatch Embedded Metric Format (EMF) — a structured
log line CloudWatch parses into metrics, so there is NO ``PutMetricData`` call
on the hot path (ADR-016). Span attributes follow the OpenTelemetry GenAI
semantic conventions so control-plane spans share the inference plane's trace
namespace.
"""

from __future__ import annotations

import json
import time
from typing import Any

_DEFAULT_NAMESPACE = "AIGateway/ControlPlane"


def emit_metric(  # noqa: PLR0913 — keyword-only EMF fields; all optional with defaults
    name: str,
    value: float,
    *,
    unit: str = "Count",
    namespace: str = _DEFAULT_NAMESPACE,
    dimensions: dict[str, str] | None = None,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Emit a single EMF metric line to stdout and return the structure.

    The returned dict is printed as JSON; CloudWatch ingests it from the Lambda
    log stream and materializes the metric. Returning it also makes the emitter
    unit-testable without parsing stdout.
    """
    dims = dimensions or {}
    metric_doc: dict[str, Any] = {
        "_aws": {
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    "Dimensions": [list(dims.keys())] if dims else [[]],
                    "Metrics": [{"Name": name, "Unit": unit}],
                }
            ],
        },
        name: value,
        **dims,
    }
    if properties:
        metric_doc.update(properties)
    print(json.dumps(metric_doc, default=str, separators=(",", ":")))  # EMF is emitted via stdout for CloudWatch
    return metric_doc


class Timer:
    """Context manager that emits a latency metric on exit.

    Usage::

        with Timer("RequestLatency", route="/budgets"):
            ...
    """

    def __init__(
        self,
        metric: str,
        *,
        namespace: str = _DEFAULT_NAMESPACE,
        clock: Any = time.monotonic,
        **dimensions: str,
    ) -> None:
        self._metric = metric
        self._namespace = namespace
        self._clock = clock
        self._dimensions = dimensions
        self._start = 0.0

    def __enter__(self) -> Timer:
        self._start = self._clock()
        return self

    def __exit__(self, *_exc: object) -> None:
        elapsed_ms = (self._clock() - self._start) * 1000.0
        emit_metric(
            self._metric,
            elapsed_ms,
            unit="Milliseconds",
            namespace=self._namespace,
            dimensions=self._dimensions,
        )


def genai_attributes(
    *,
    operation: str,
    model: str = "",
    provider: str = "",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> dict[str, Any]:
    """Build OTEL GenAI semantic-convention span attributes.

    Mirrors the ``gen_ai.*`` keys the inference plane emits, so a control-plane
    span (e.g. a guardrail-threshold change) joins the same trace vocabulary.
    """
    attrs: dict[str, Any] = {"gen_ai.operation.name": operation}
    if provider:
        attrs["gen_ai.provider.name"] = provider
    if model:
        attrs["gen_ai.request.model"] = model
    if input_tokens is not None:
        attrs["gen_ai.usage.input_tokens"] = input_tokens
    if output_tokens is not None:
        attrs["gen_ai.usage.output_tokens"] = output_tokens
    return attrs
