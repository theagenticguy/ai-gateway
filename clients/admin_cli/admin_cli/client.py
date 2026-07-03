"""Auth + HTTP for the AI Gateway admin CLI.

``GatewayClient`` acquires a Cognito M2M ``client_credentials`` access token,
attaches it as a bearer on every admin-API call, refreshes once on a 401, and
parses the gateway response envelope (``src/gwcore/responses.py``) into a plain
dict — raising :class:`GatewayError` for the error envelope.

The CLI is deliberately thin: it does not import the server models and does not
re-serialize bodies. A pre-built JSON string is sent verbatim.

Config is env/flag only — nothing is hardcoded:

- ``GATEWAY_ADMIN_URL``      — admin_api stage invoke URL (``.../{env}``).
- ``GATEWAY_CLIENT_ID``      — Cognito M2M client id (admin-scoped).
- ``GATEWAY_CLIENT_SECRET``  — Cognito M2M client secret.
- ``GATEWAY_TOKEN_ENDPOINT`` — full token URL, OR derive it from
  ``COGNITO_DOMAIN`` + ``AWS_REGION``.
- ``GATEWAY_ADMIN_SCOPE``    — override the requested scope
  (default ``https://gateway.internal/admin``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

# The admin scope the CLI must request. Team clients only hold ``invoke`` —
# the client used here MUST be an admin-scoped M2M client (see README).
DEFAULT_ADMIN_SCOPE = "https://gateway.internal/admin"

# Retry the request exactly once after refreshing the token on a 401.
_UNAUTHORIZED = 401

# Cognito access-token safety margin: refresh a bit before the stated expiry.
_EXPIRY_SKEW_SECONDS = 30.0


class GatewayError(Exception):
    """A control-plane error-envelope, surfaced as a clean CLI failure.

    Carries the ``error.code`` / ``error.message`` (+ optional ``details``) from
    the gateway so the CLI can print a readable message and exit non-zero,
    rather than dumping a stack trace.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        details: Any = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status = status
        self.details = details
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class Config:
    """Resolved connection + auth configuration (from env, overridable by flags)."""

    admin_url: str
    client_id: str
    client_secret: str
    token_endpoint: str
    scope: str = DEFAULT_ADMIN_SCOPE

    @staticmethod
    def _derive_token_endpoint(env: dict[str, str]) -> str | None:
        """Build the Cognito token endpoint from ``COGNITO_DOMAIN`` + ``AWS_REGION``."""
        domain = env.get("COGNITO_DOMAIN")
        region = env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION")
        if domain and region:
            return f"https://{domain}.auth.{region}.amazoncognito.com/oauth2/token"
        return None

    @classmethod
    def from_env(
        cls,
        env: dict[str, str] | None = None,
        *,
        admin_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_endpoint: str | None = None,
        scope: str | None = None,
    ) -> Config:
        """Resolve config from ``env`` (defaults to ``os.environ``), flags win.

        Raises:
            GatewayError: if any required value is missing, with a message that
                names the missing env var so the operator can fix it.
        """
        env = dict(os.environ if env is None else env)

        admin_url = admin_url or env.get("GATEWAY_ADMIN_URL")
        client_id = client_id or env.get("GATEWAY_CLIENT_ID")
        client_secret = client_secret or env.get("GATEWAY_CLIENT_SECRET")
        token_endpoint = (
            token_endpoint or env.get("GATEWAY_TOKEN_ENDPOINT") or cls._derive_token_endpoint(env)
        )
        scope = scope or env.get("GATEWAY_ADMIN_SCOPE") or DEFAULT_ADMIN_SCOPE

        missing: list[str] = []
        if not admin_url:
            missing.append("GATEWAY_ADMIN_URL")
        if not client_id:
            missing.append("GATEWAY_CLIENT_ID")
        if not client_secret:
            missing.append("GATEWAY_CLIENT_SECRET")
        if not token_endpoint:
            missing.append("GATEWAY_TOKEN_ENDPOINT (or COGNITO_DOMAIN + AWS_REGION)")
        if missing:
            msg = "Missing required configuration: " + ", ".join(missing)
            raise GatewayError("config_error", msg)

        return cls(
            admin_url=admin_url.rstrip("/"),  # type: ignore[union-attr]
            client_id=client_id,  # type: ignore[arg-type]
            client_secret=client_secret,  # type: ignore[arg-type]
            token_endpoint=token_endpoint,  # type: ignore[arg-type]
            scope=scope,
        )


class GatewayClient:
    """Sync client: M2M token acquisition, bearer auth, 401-refresh, envelope parse.

    The token is cached in-memory for the life of the process (Cognito TTL is
    3600s). On a 401, the token is dropped, re-fetched, and the request retried
    exactly once.
    """

    def __init__(
        self,
        config: Config,
        *,
        http: httpx.Client | None = None,
        token_http: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._config = config
        # Separate clients so tests can inject one transport that serves both the
        # absolute token endpoint and the base_url'd admin API.
        self._http = http or httpx.Client(base_url=config.admin_url, timeout=timeout)
        self._token_http = token_http or httpx.Client(timeout=timeout)
        self._token: str | None = None

    # ── context management ────────────────────────────────────────────────────

    def __enter__(self) -> GatewayClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP clients."""
        self._http.close()
        if self._token_http is not self._http:
            self._token_http.close()

    # ── auth ──────────────────────────────────────────────────────────────────

    def _fetch_token(self) -> str:
        """POST the Cognito ``client_credentials`` grant and return the access token."""
        resp = self._token_http.post(
            self._config.token_endpoint,
            data={"grant_type": "client_credentials", "scope": self._config.scope},
            auth=httpx.BasicAuth(self._config.client_id, self._config.client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.is_error:  # 4xx/5xx from the token endpoint
            raise self._error_from_response(resp, fallback_code="token_error")
        payload = resp.json()
        token = payload.get("access_token")
        if not token:
            raise GatewayError(
                "token_error",
                "Token endpoint returned no access_token",
                status=resp.status_code,
                details=payload,
            )
        return str(token)

    def _get_token(self, *, force: bool = False) -> str:
        """Return a cached token, fetching a fresh one when missing or forced."""
        if force or self._token is None:
            self._token = self._fetch_token()
        return self._token

    # ── requests ──────────────────────────────────────────────────────────────

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: str | None = None,
    ) -> Any:
        """Make an authed admin-API call and return the parsed JSON body.

        Sends ``body`` (a pre-serialized JSON string) verbatim. Refreshes the
        token and retries once on a 401. Raises :class:`GatewayError` for any
        error envelope.
        """
        resp = self._send(method, path, params=params, body=body, token=self._get_token())
        if resp.status_code == _UNAUTHORIZED:
            # Token likely expired/revoked — refresh once and retry.
            resp = self._send(
                method, path, params=params, body=body, token=self._get_token(force=True)
            )

        if resp.is_error:  # 4xx/5xx after the (single) refresh-retry
            raise self._error_from_response(resp)
        return self._parse_body(resp)

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        body: str | None,
        token: str,
    ) -> httpx.Response:
        """Issue a single HTTP request with the bearer token attached."""
        headers = {"Authorization": f"Bearer {token}"}
        content: bytes | None = None
        if body is not None:
            content = body.encode("utf-8")
            headers["Content-Type"] = "application/json"
        # Drop None-valued params so an empty cursor/filter is omitted.
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        return self._http.request(
            method.upper(),
            path,
            params=clean_params or None,
            content=content,
            headers=headers,
        )

    # ── envelope parsing ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_body(resp: httpx.Response) -> Any:
        """Parse a success response body. Empty body (e.g. 204/304) → ``None``."""
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            # Not JSON — return the raw text so nothing is silently dropped.
            return resp.text

    @staticmethod
    def _error_from_response(
        resp: httpx.Response, *, fallback_code: str = "http_error"
    ) -> GatewayError:
        """Map a 4xx/5xx response to a :class:`GatewayError`.

        The gateway error envelope is ``{"error": {"code", "message", details?}}``
        (``src/gwcore/errors.py``). Fall back to a generic message when the body
        is not the expected shape.
        """
        code = fallback_code
        message = f"HTTP {resp.status_code}"
        details: Any = None
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict):
                code = str(err.get("code", code))
                message = str(err.get("message", message))
                details = err.get("details")
            elif isinstance(err, str):
                message = err
        return GatewayError(code, message, status=resp.status_code, details=details)
