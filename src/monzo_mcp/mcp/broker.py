"""Request-scoped access-token provider backed by an authenticated HTTP broker."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Literal

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, SecretStr, ValidationError

from monzo_mcp.client import AccessTokenProvider, MonzoClientError

if TYPE_CHECKING:
    from starlette.requests import Request

    from monzo_mcp.mcp.settings import AccessTokenBrokerSettings

_MAX_DELEGATION_HEADER_BYTES = 8192


class CredentialBrokerError(MonzoClientError):
    """A safe error raised by the configured access-token broker."""


class CredentialBrokerAuthorizationError(CredentialBrokerError):
    """The request has no usable broker delegation."""


class CredentialBrokerUnavailableError(CredentialBrokerError):
    """The broker could not return a usable access token."""


class CredentialBrokerReauthenticationRequiredError(CredentialBrokerError):
    """The broker requires the user to complete provider authorization again."""


class BrokerAccessTokenResponse(BaseModel):
    """The only secret material accepted from the credential broker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: SecretStr
    token_type: Literal["Bearer"]
    expires_at: AwareDatetime | None = None


@dataclass(frozen=True, slots=True)
class BrokerAccessTokenProvider(AccessTokenProvider):
    """Fetch one access token per provider request without caching it."""

    http_client: httpx.AsyncClient
    settings: AccessTokenBrokerSettings
    delegation: SecretStr

    async def get_access_token(self) -> str:
        """Fetch the currently usable access token."""
        return await self._request_access_token(rejected_fingerprint=None)

    async def refresh_after_rejection(self, rejected_access_token: str) -> str:
        """Ask the broker to replace a token only if it remains current."""
        fingerprint = sha256(rejected_access_token.encode()).hexdigest()
        return await self._request_access_token(
            rejected_fingerprint=fingerprint,
        )

    async def _request_access_token(
        self,
        *,
        rejected_fingerprint: str | None,
    ) -> str:
        body = (
            {}
            if rejected_fingerprint is None
            else {"rejected_access_token_sha256": rejected_fingerprint}
        )
        try:
            response = await self.http_client.post(
                self.settings.url,
                json=body,
                headers={
                    "Accept": "application/json",
                    "Authorization": self.delegation.get_secret_value(),
                    "Content-Type": "application/json",
                },
                timeout=self.settings.timeout_seconds,
            )
        except httpx.TimeoutException:
            raise CredentialBrokerUnavailableError(
                "The credential broker timed out"
            ) from None
        except httpx.RequestError:
            raise CredentialBrokerUnavailableError(
                "The credential broker could not be reached"
            ) from None

        if response.status_code in {401, 403}:
            raise CredentialBrokerAuthorizationError(
                "Credential broker authorization failed"
            )
        if response.status_code == 409:
            raise CredentialBrokerReauthenticationRequiredError(
                "Monzo authorization must be completed in the MCP client"
            )
        if response.status_code >= 300:
            raise CredentialBrokerUnavailableError(
                "The credential broker could not issue an access token"
            )
        try:
            payload = BrokerAccessTokenResponse.model_validate_json(response.content)
        except ValidationError:
            raise CredentialBrokerUnavailableError(
                "The credential broker returned an invalid response"
            ) from None
        return payload.access_token.get_secret_value()


@dataclass(frozen=True, slots=True)
class BrokerAccessTokenProviderFactory:
    """Create a provider bound to the current MCP HTTP request."""

    http_client: httpx.AsyncClient
    settings: AccessTokenBrokerSettings

    def create(self, request: Request | None) -> AccessTokenProvider:
        """Extract one exact delegation header without retaining the request."""
        if request is None:
            raise CredentialBrokerAuthorizationError(
                "Credential delegation is unavailable outside HTTP requests"
            )
        supplied = request.headers.getlist(self.settings.delegation_header_name)
        if len(supplied) != 1:
            raise CredentialBrokerAuthorizationError(
                "Exactly one credential delegation header is required"
            )
        delegation = supplied[0]
        if (
            len(delegation.encode()) > _MAX_DELEGATION_HEADER_BYTES
            or not delegation.startswith("Bearer ")
            or not delegation.removeprefix("Bearer ").strip()
        ):
            raise CredentialBrokerAuthorizationError("Credential delegation is invalid")
        return BrokerAccessTokenProvider(
            http_client=self.http_client,
            settings=self.settings,
            delegation=SecretStr(delegation),
        )
