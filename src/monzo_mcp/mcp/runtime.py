"""Process-lifetime ownership of HTTP resources and access-token providers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from typing import TYPE_CHECKING

import httpx
from aiomonzo import OAuthAccessTokenProvider

from monzo_mcp.credentials import (
    ClientCredentialStore,
    CredentialConfigurationError,
)
from monzo_mcp.mcp.broker import BrokerAccessTokenProviderFactory
from monzo_mcp.mcp.context import AppContext
from monzo_mcp.mcp.local import LocalAccessTokenProviderFactory
from monzo_mcp.mcp.settings import (
    AccessTokenBrokerSettings,
    AccessTokenProviderMode,
    CredentialSettings,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from monzo_mcp.mcp.settings import AccessTokenProviderSettings

_HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
_HTTP_LIMITS = httpx.Limits(
    max_connections=20,
    max_keepalive_connections=10,
    keepalive_expiry=30.0,
)
_REFRESH_SKEW = timedelta(seconds=30)


@asynccontextmanager
async def open_runtime(
    provider_settings: AccessTokenProviderSettings,
    *,
    http_client: httpx.AsyncClient | None = None,
    monzo_api_base_url: str = "https://api.monzo.com",
) -> AsyncIterator[AppContext]:
    """Open one pooled HTTP client and the selected provider for its lifespan."""
    managed_http_client = http_client is None
    resolved_http_client = http_client or httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT,
        limits=_HTTP_LIMITS,
        follow_redirects=False,
        headers={"User-Agent": "monzo-mcp/0.1.0"},
    )
    try:
        if isinstance(provider_settings, AccessTokenBrokerSettings):
            yield AppContext(
                http_client=resolved_http_client,
                access_token_provider_factory=BrokerAccessTokenProviderFactory(
                    http_client=resolved_http_client,
                    settings=provider_settings,
                ),
                access_token_provider_mode=AccessTokenProviderMode.BROKER,
                monzo_api_base_url=monzo_api_base_url,
            )
        elif isinstance(provider_settings, CredentialSettings):
            store = ClientCredentialStore(
                credential_dir=provider_settings.credential_dir,
                key_file=provider_settings.key_file,
            )
            async with store:
                oauth = await store.load_oauth()
                token = await store.load()
                if oauth is None or token is None:
                    raise CredentialConfigurationError(
                        "Local Monzo credentials are incomplete; run human login first"
                    )
                if (
                    token.refresh_token is None
                    or token.expires_at is None
                    or token.token_type.lower() != "bearer"
                    or (
                        token.client_id is not None
                        and token.client_id != oauth.client_id
                    )
                ):
                    raise CredentialConfigurationError(
                        "Local Monzo credentials are invalid; run human login again"
                    )
                provider = OAuthAccessTokenProvider(
                    http_client=resolved_http_client,
                    token_store=store,
                    oauth=oauth,
                    api_base_url=monzo_api_base_url,
                    refresh_skew=_REFRESH_SKEW,
                )
                try:
                    yield AppContext(
                        http_client=resolved_http_client,
                        access_token_provider_factory=LocalAccessTokenProviderFactory(
                            provider=provider
                        ),
                        access_token_provider_mode=AccessTokenProviderMode.LOCAL,
                        monzo_api_base_url=monzo_api_base_url,
                    )
                finally:
                    provider.forget_oauth_client()
        else:
            raise TypeError("Unsupported access-token provider settings")
    finally:
        if managed_http_client:
            await resolved_http_client.aclose()
