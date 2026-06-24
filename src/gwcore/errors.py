"""Typed exception hierarchy for the control plane.

Handlers raise these; ``gwcore.responses.error_response`` maps them to HTTP
status codes and a consistent error envelope. This replaces the ad-hoc
``return _build_response(4xx, {"error": "..."})`` scattered across handlers.
"""

from __future__ import annotations

from typing import Any


class ControlPlaneError(Exception):
    """Base error for the control plane.

    Carries an HTTP ``status``, a stable machine-readable ``code``, a
    human ``message``, and optional structured ``details``.
    """

    status: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        code: str | None = None,
    ) -> None:
        self.message = message or self.__class__.__doc__ or "Error"
        self.details = details or {}
        if code is not None:
            self.code = code
        super().__init__(self.message)

    def to_body(self) -> dict[str, Any]:
        """Render the error envelope body."""
        body: dict[str, Any] = {"error": {"code": self.code, "message": self.message}}
        if self.details:
            body["error"]["details"] = self.details
        return body


class ValidationFailedError(ControlPlaneError):
    """Request failed validation."""

    status = 400
    code = "validation_failed"


class UnauthorizedError(ControlPlaneError):
    """Authentication is missing or invalid."""

    status = 401
    code = "unauthorized"


class ForbiddenError(ControlPlaneError):
    """Authenticated but not permitted."""

    status = 403
    code = "forbidden"


class NotFoundError(ControlPlaneError):
    """Resource does not exist."""

    status = 404
    code = "not_found"


class ConflictError(ControlPlaneError):
    """Resource state conflicts with the request (e.g. duplicate, version mismatch)."""

    status = 409
    code = "conflict"


class UpstreamError(ControlPlaneError):
    """A downstream dependency (DynamoDB, Cognito, etc.) failed."""

    status = 502
    code = "upstream_error"
