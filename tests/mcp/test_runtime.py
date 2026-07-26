"""Access-token provider runtime lifecycle tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

import httpx
import pytest
from aiomonzo import (
    AccessTokenProvider,
    MonzoTokenStoreError,
    OAuthClientConfig,
    OAuthToken,
)
from pydantic import AnyHttpUrl, SecretStr

from monzo_mcp.credentials import (
    ClientCredentialStore,
    CredentialConfigurationError,
    CredentialLockedError,
)
from monzo_mcp.mcp.runtime import open_runtime
from monzo_mcp.mcp.settings import (
    AccessTokenBrokerSettings,
    AccessTokenProviderMode,
    CredentialSettings,
)
from tests.mcp.monzo_responses import TOKEN

if TYPE_CHECKING:
    from pathlib import Path

_BROKER_URL = "https://credential-broker.test/access-token"
_MONZO_URL = "https://api.monzo.test"


def _oauth() -> OAuthClientConfig:
    return OAuthClientConfig(
        client_id="client_1",
        client_secret=SecretStr("client-sensitive"),
        redirect_uri=AnyHttpUrl("http://127.0.0.1:8765/oauth/callback"),
    )


def _token(
    *,
    access_token: str | None = None,
    expires_at: datetime | None = None,
) -> OAuthToken:
    return OAuthToken(
        access_token=SecretStr(access_token or "access-old"),
        refresh_token=SecretStr("refresh-old"),
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
        client_id="client_1",
        user_id="user_1",
    )


def _settings(tmp_path: Path) -> CredentialSettings:
    return CredentialSettings(
        credential_dir=tmp_path / "credentials",
        key_file=tmp_path / "key",
    )


async def _write_credentials(
    settings: CredentialSettings,
    *,
    token: OAuthToken | None = None,
) -> None:
    async with ClientCredentialStore(
        credential_dir=settings.credential_dir,
        key_file=settings.key_file,
        create_key=True,
    ) as store:
        await store.save_oauth(_oauth())
        if token is not None:
            await store.save(token)


async def test_local_runtime_supplies_stored_token_without_request_delegation(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    await _write_credentials(settings, token=_token())
    upstream_requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal upstream_requests
        upstream_requests += 1
        return httpx.Response(500)

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client,
        open_runtime(
            settings,
            http_client=client,
            monzo_api_base_url=_MONZO_URL,
        ) as context,
    ):
        provider = context.access_token_provider_factory.create(None)
        access_token = await provider.get_access_token()

    assert context.access_token_provider_mode is AccessTokenProviderMode.LOCAL
    assert access_token == "access-old"
    assert upstream_requests == 0


@pytest.mark.parametrize(
    "credential_state",
    ["missing", "oauth-only", "non-refreshable"],
)
async def test_local_runtime_fails_before_start_for_incomplete_credentials(
    tmp_path: Path,
    credential_state: str,
) -> None:
    settings = _settings(tmp_path)
    if credential_state == "oauth-only":
        await _write_credentials(settings)
    elif credential_state == "non-refreshable":
        await _write_credentials(
            settings,
            token=OAuthToken(
                access_token=SecretStr("access-static"),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                client_id="client_1",
            ),
        )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500))
    ) as client:
        with pytest.raises(CredentialConfigurationError):
            async with open_runtime(settings, http_client=client):
                pytest.fail("Incomplete local runtime unexpectedly started")


async def test_local_runtime_serializes_refresh_and_persists_rotation(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    await _write_credentials(
        settings,
        token=_token(expires_at=datetime.now(UTC) - timedelta(seconds=1)),
    )
    refreshes = 0
    received_forms: list[dict[str, list[str]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refreshes
        assert request.url.path == "/oauth2/token"
        refreshes += 1
        received_forms.append(parse_qs(request.content.decode()))
        await asyncio.sleep(0)
        return httpx.Response(200, content=TOKEN)

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client,
        open_runtime(
            settings,
            http_client=client,
            monzo_api_base_url=_MONZO_URL,
        ) as context,
    ):
        providers = [
            context.access_token_provider_factory.create(None) for _ in range(20)
        ]
        access_tokens = await asyncio.gather(
            *(provider.get_access_token() for provider in providers)
        )

    assert access_tokens == ["access-new"] * 20
    assert refreshes == 1
    assert received_forms == [
        {
            "grant_type": ["refresh_token"],
            "client_id": ["client_1"],
            "client_secret": ["client-sensitive"],
            "refresh_token": ["refresh-old"],
        }
    ]
    async with ClientCredentialStore(
        credential_dir=settings.credential_dir,
        key_file=settings.key_file,
    ) as store:
        persisted = await store.load()
    assert persisted is not None
    assert persisted.access_token.get_secret_value() == "access-new"
    assert persisted.refresh_token is not None
    assert persisted.refresh_token.get_secret_value() == "refresh-new"

    restart_requests = 0

    def fail_on_request(_request: httpx.Request) -> httpx.Response:
        nonlocal restart_requests
        restart_requests += 1
        return httpx.Response(500)

    async with (
        httpx.AsyncClient(
            transport=httpx.MockTransport(fail_on_request)
        ) as restart_client,
        open_runtime(
            settings,
            http_client=restart_client,
            monzo_api_base_url=_MONZO_URL,
        ) as restarted,
    ):
        restarted_token = await restarted.access_token_provider_factory.create(
            None
        ).get_access_token()
    assert restarted_token == "access-new"
    assert restart_requests == 0


async def test_local_runtime_reuses_concurrently_rotated_rejected_token(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    await _write_credentials(settings, token=_token())
    refreshes = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal refreshes
        refreshes += 1
        await asyncio.sleep(0)
        return httpx.Response(200, content=TOKEN)

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client,
        open_runtime(
            settings,
            http_client=client,
            monzo_api_base_url=_MONZO_URL,
        ) as context,
    ):
        provider = context.access_token_provider_factory.create(None)
        replacements = await asyncio.gather(
            *(provider.refresh_after_rejection("access-old") for _ in range(10))
        )

    assert replacements == ["access-new"] * 10
    assert refreshes == 1


async def test_local_runtime_releases_lock_and_forgets_decrypted_state(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    await _write_credentials(settings, token=_token())
    provider: AccessTokenProvider | None = None
    second = ClientCredentialStore(
        credential_dir=settings.credential_dir,
        key_file=settings.key_file,
    )

    async with (
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _request: httpx.Response(500))
        ) as client,
        open_runtime(settings, http_client=client) as context,
    ):
        provider = context.access_token_provider_factory.create(None)
        with pytest.raises(CredentialLockedError):
            await second.__aenter__()

    assert provider is not None
    with pytest.raises(MonzoTokenStoreError):
        await provider.get_access_token()
    async with second:
        assert await second.load() is not None


async def test_broker_runtime_never_requires_local_credential_paths() -> None:
    async with (
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _request: httpx.Response(500))
        ) as client,
        open_runtime(
            AccessTokenBrokerSettings(url=_BROKER_URL),
            http_client=client,
        ) as context,
    ):
        assert context.access_token_provider_mode is AccessTokenProviderMode.BROKER
