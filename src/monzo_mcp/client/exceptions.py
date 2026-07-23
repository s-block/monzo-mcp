"""Typed and secret-safe exceptions for the Monzo client."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from pydantic import ValidationError

from monzo_mcp.client.models import MonzoErrorPayload


class MonzoClientError(Exception):
    """Base class for all errors raised by this client."""


class MonzoConfigurationError(MonzoClientError):
    """The client is missing required or compatible configuration."""


class MonzoRequestValidationError(MonzoClientError):
    """A public method received invalid request data."""


class MonzoClosedError(MonzoClientError):
    """A request was attempted after the client was closed."""


class MonzoTokenStoreError(MonzoClientError):
    """An injected token store failed to load, save, or clear tokens."""


class MonzoTransportError(MonzoClientError):
    """A request failed before a usable HTTP response was received."""


class MonzoTimeoutError(MonzoTransportError):
    """A configured connect, read, write, or pool timeout elapsed."""


class MonzoResponseDecodeError(MonzoClientError):
    """Monzo returned a successful response that was not valid JSON."""

    def __init__(self, model_name: str) -> None:
        super().__init__(f"Monzo returned invalid JSON for {model_name}")
        self.model_name = model_name


class MonzoResponseValidationError(MonzoClientError):
    """Monzo returned JSON that did not match the expected schema."""

    def __init__(self, model_name: str) -> None:
        super().__init__(f"Monzo returned an invalid {model_name} response")
        self.model_name = model_name


class MonzoHTTPError(MonzoClientError):
    """Monzo returned a non-successful HTTP response."""

    def __init__(
        self,
        *,
        status_code: int,
        message: str,
        code: str | None = None,
        oauth_error: str | None = None,
        request_id: str | None = None,
    ) -> None:
        summary = f"Monzo API request failed with HTTP {status_code}: {message}"
        if code:
            summary = f"{summary} ({code})"
        super().__init__(summary)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.oauth_error = oauth_error
        self.request_id = request_id


class MonzoAuthenticationError(MonzoHTTPError):
    """The access token or OAuth grant was rejected."""


class MonzoReauthenticationRequired(MonzoAuthenticationError):  # noqa: N818
    """No usable refresh token remains, so user authorization is required."""

    def __init__(self, message: str = "User authorization is required") -> None:
        super().__init__(status_code=401, message=message)


class MonzoPermissionError(MonzoHTTPError):
    """The token is authenticated but lacks permission for the operation."""


class MonzoRateLimitError(MonzoHTTPError):
    """Monzo rejected a request because the rate limit was exceeded."""

    def __init__(
        self,
        *,
        status_code: int,
        message: str,
        code: str | None = None,
        oauth_error: str | None = None,
        request_id: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(
            status_code=status_code,
            message=message,
            code=code,
            oauth_error=oauth_error,
            request_id=request_id,
        )
        self.retry_after = retry_after


def _retry_after_seconds(
    value: str | None,
    *,
    now: datetime | None = None,
) -> float | None:
    """Parse either supported Retry-After representation into seconds."""
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        reference = now or datetime.now(UTC)
        return max(0.0, (retry_at - reference).total_seconds())


def _request_id(response: httpx.Response) -> str | None:
    for header in ("x-request-id", "x-monzo-request-id", "request-id"):
        if value := response.headers.get(header):
            return str(value)
    return None


def _error_payload(response: httpx.Response) -> MonzoErrorPayload:
    if not response.content.strip():
        return MonzoErrorPayload()
    try:
        return MonzoErrorPayload.model_validate_json(response.content)
    except ValidationError:
        return MonzoErrorPayload()


def _exception_from_response(response: httpx.Response) -> MonzoHTTPError:
    """Map an unsuccessful response without retaining its sensitive body."""
    payload = _error_payload(response)
    default_message = response.reason_phrase or "Request failed"
    message = payload.message or payload.error or default_message
    request_id = _request_id(response)
    if response.status_code == httpx.codes.UNAUTHORIZED or payload.error in {
        "invalid_client",
        "invalid_grant",
        "invalid_request",
        "invalid_token",
        "unauthorized_client",
        "unsupported_grant_type",
    }:
        return MonzoAuthenticationError(
            status_code=response.status_code,
            message=message,
            code=payload.code,
            oauth_error=payload.error,
            request_id=request_id,
        )
    if response.status_code == httpx.codes.FORBIDDEN:
        if payload.message is None:
            message = "Access is not approved; confirm the request in the Monzo app"
        return MonzoPermissionError(
            status_code=response.status_code,
            message=message,
            code=payload.code,
            oauth_error=payload.error,
            request_id=request_id,
        )
    if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
        return MonzoRateLimitError(
            status_code=response.status_code,
            message=message,
            code=payload.code,
            oauth_error=payload.error,
            request_id=request_id,
            retry_after=_retry_after_seconds(response.headers.get("retry-after")),
        )
    return MonzoHTTPError(
        status_code=response.status_code,
        message=message,
        code=payload.code,
        oauth_error=payload.error,
        request_id=request_id,
    )
