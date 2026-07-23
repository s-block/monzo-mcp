"""Async OAuth token acquisition and rotation for Monzo."""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from hmac import compare_digest
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlencode

import httpx
from pydantic import AnyHttpUrl, ValidationError

from monzo_mcp.client.exceptions import (
    MonzoConfigurationError,
    MonzoReauthenticationRequired,
    MonzoResponseDecodeError,
    MonzoResponseValidationError,
    MonzoTimeoutError,
    MonzoTokenStoreError,
    MonzoTransportError,
    _exception_from_response,
)
from monzo_mcp.client.models import (
    AuthorizationRequest,
    OAuthClientConfig,
    OAuthToken,
    _OAuthTokenResponse,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_AUTHORIZE_URL = "https://auth.monzo.com/"
_TOKEN_PATH = "/oauth2/token"  # noqa: S105


class TokenStore(Protocol):
    """Async persistence contract for one Monzo OAuth token set."""

    async def load(self) -> OAuthToken | None:
        """Load the current token set, if present."""

    async def save(self, token: OAuthToken) -> None:
        """Atomically replace the current token set."""

    async def clear(self) -> None:
        """Remove the current token set."""


class AccessTokenProvider(Protocol):
    """Supply request-time access tokens without prescribing their storage."""

    async def get_access_token(self) -> str:
        """Return a currently usable access token."""

    async def refresh_after_rejection(self, rejected_access_token: str) -> str:
        """Return a replacement after the provider rejected an access token."""


class InMemoryTokenStore:
    """Process-local token storage with no filesystem I/O."""

    def __init__(self, token: OAuthToken | None = None) -> None:
        self._token = token

    async def load(self) -> OAuthToken | None:
        """Return the current immutable token object."""
        return self._token

    async def save(self, token: OAuthToken) -> None:
        """Replace the current token object."""
        self._token = token

    async def clear(self) -> None:
        """Forget the current token object."""
        self._token = None


def validate_oauth_state(*, expected: str, returned: str) -> None:
    """Validate the OAuth callback state in constant time."""
    if not expected or not returned or not compare_digest(expected, returned):
        raise MonzoConfigurationError("OAuth state validation failed")


class OAuthAccessTokenProvider:
    """Coordinate token access and one-time refresh-token rotation."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        token_store: TokenStore,
        oauth: OAuthClientConfig | None,
        api_base_url: str,
        refresh_skew: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if refresh_skew < timedelta(0):
            raise MonzoConfigurationError("refresh_skew must not be negative")
        self._http_client = http_client
        self._token_store = token_store
        self._oauth = oauth
        self._api_base_url = api_base_url.rstrip("/")
        self._refresh_skew = refresh_skew
        self._clock = clock or (lambda: datetime.now(UTC))
        self._refresh_lock = asyncio.Lock()

    def create_authorization_request(
        self,
        *,
        state: str | None = None,
    ) -> AuthorizationRequest:
        """Create a browser authorization URL and its callback state."""
        oauth = self._require_oauth()
        callback_state = secrets.token_urlsafe(32) if state is None else state
        if not callback_state:
            raise MonzoConfigurationError("OAuth state must not be empty")
        query = urlencode(
            {
                "client_id": oauth.client_id,
                "redirect_uri": str(oauth.redirect_uri),
                "response_type": "code",
                "state": callback_state,
            }
        )
        return AuthorizationRequest(
            url=AnyHttpUrl(f"{_AUTHORIZE_URL}?{query}"),
            state=callback_state,
        )

    async def exchange_authorization_code(
        self,
        code: str,
        *,
        expected_state: str,
        returned_state: str,
    ) -> OAuthToken:
        """Exchange a validated authorization code and persist its token set."""
        validate_oauth_state(expected=expected_state, returned=returned_state)
        if not code:
            raise MonzoConfigurationError("Authorization code must not be empty")
        oauth = self._require_oauth()
        token = await self._request_token(
            {
                "grant_type": "authorization_code",
                "client_id": oauth.client_id,
                "client_secret": oauth.client_secret.get_secret_value(),
                "redirect_uri": str(oauth.redirect_uri),
                "code": code,
            }
        )
        await self._save(token)
        return token

    async def get_access_token(self) -> str:
        """Return a usable token, refreshing it shortly before expiry."""
        token = await self._load_required()
        now = self._now()
        if token.is_expiring(at=now, skew=self._refresh_skew):
            if token.refresh_token is None:
                raise MonzoReauthenticationRequired(
                    "The access token expired and cannot be refreshed"
                )
            token = await self._refresh(
                expected_access_token=token.access_token.get_secret_value()
            )
        return token.access_token.get_secret_value()

    async def refresh_access_token(self) -> OAuthToken:
        """Rotate the current refresh token, coordinating concurrent callers."""
        token = await self._load_required()
        return await self._refresh(
            expected_access_token=token.access_token.get_secret_value()
        )

    async def refresh_after_rejection(self, rejected_access_token: str) -> str:
        """Refresh unless another request already replaced the rejected token."""
        token = await self._refresh(expected_access_token=rejected_access_token)
        return token.access_token.get_secret_value()

    async def clear(self) -> None:
        """Clear stored credentials."""
        try:
            await self._token_store.clear()
        except Exception as error:
            raise MonzoTokenStoreError("Token store clear failed") from error

    def forget_oauth_client(self) -> None:
        """Drop the decrypted OAuth client configuration held by this instance."""
        self._oauth = None

    async def _refresh(self, *, expected_access_token: str) -> OAuthToken:
        async with self._refresh_lock:
            current = await self._load_required()
            if current.access_token.get_secret_value() != expected_access_token:
                return current
            oauth = self._require_oauth()
            if current.refresh_token is None:
                raise MonzoReauthenticationRequired(
                    "The access token cannot be refreshed; authorize again"
                )
            replacement = await self._request_token(
                {
                    "grant_type": "refresh_token",
                    "client_id": oauth.client_id,
                    "client_secret": oauth.client_secret.get_secret_value(),
                    "refresh_token": current.refresh_token.get_secret_value(),
                }
            )
            if replacement.refresh_token is None:
                raise MonzoResponseValidationError("OAuthToken")
            await self._save(replacement)
            return replacement

    async def _request_token(self, form: Mapping[str, str]) -> OAuthToken:
        try:
            response = await self._http_client.post(
                f"{self._api_base_url}{_TOKEN_PATH}",
                data=form,
                headers={"Accept": "application/json"},
            )
        except httpx.TimeoutException:
            raise MonzoTimeoutError("Monzo OAuth request timed out") from None
        except httpx.RequestError:
            raise MonzoTransportError("Monzo OAuth request failed") from None

        if response.is_error:
            raise _exception_from_response(response)

        try:
            wire_token = _OAuthTokenResponse.model_validate_json(response.content)
        except ValidationError as error:
            if any(item["type"] == "json_invalid" for item in error.errors()):
                raise MonzoResponseDecodeError("OAuthToken") from None
            raise MonzoResponseValidationError("OAuthToken") from None
        return wire_token.to_token(received_at=self._now())

    async def _load_required(self) -> OAuthToken:
        try:
            token = await self._token_store.load()
        except Exception as error:
            raise MonzoTokenStoreError("Token store load failed") from error
        if token is None:
            raise MonzoReauthenticationRequired("No access token is available")
        return token

    async def _save(self, token: OAuthToken) -> None:
        try:
            await self._token_store.save(token)
        except Exception as error:
            raise MonzoTokenStoreError("Token store save failed") from error

    def _require_oauth(self) -> OAuthClientConfig:
        if self._oauth is None:
            raise MonzoConfigurationError(
                "OAuth client credentials are required for this operation"
            )
        return self._oauth

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise MonzoConfigurationError("OAuth clock must return an aware datetime")
        return now
