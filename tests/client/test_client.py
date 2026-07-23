"""Construction, storage failures, and package contract tests."""

from importlib.resources import files

import httpx
import pytest

from monzo_mcp.client import (
    MonzoClient,
    MonzoConfigurationError,
    MonzoTokenStoreError,
    OAuthToken,
)


class _BrokenTokenStore:
    async def load(self) -> OAuthToken | None:
        raise RuntimeError("database details must remain chained only")

    async def save(self, token: OAuthToken) -> None:
        del token
        raise RuntimeError("save details")

    async def clear(self) -> None:
        raise RuntimeError("clear details")


def test_package_publishes_typing_marker() -> None:
    marker = files("monzo_mcp").joinpath("py.typed")

    assert marker.is_file()


def test_constructor_rejects_ambiguous_credentials_and_bad_base_url() -> None:
    with pytest.raises(MonzoConfigurationError):
        MonzoClient(access_token="token", token_store=_BrokenTokenStore())
    with pytest.raises(MonzoConfigurationError):
        MonzoClient(access_token="")
    with pytest.raises(MonzoConfigurationError):
        MonzoClient(access_token="token", api_base_url="ftp://example.test")
    with pytest.raises(MonzoConfigurationError):
        MonzoClient(
            access_token="token", api_base_url="https://example.test?secret=value"
        )


async def test_token_store_failure_has_stable_safe_message() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500))
    )
    client = MonzoClient(token_store=_BrokenTokenStore(), http_client=http)

    with pytest.raises(MonzoTokenStoreError) as captured:
        await client.who_am_i()

    assert str(captured.value) == "Token store load failed"
    await client.aclose()
    await http.aclose()


async def test_owned_http_client_is_closed_idempotently() -> None:
    client = MonzoClient(access_token="access-static")
    owned_http = client._transport._http_client

    await client.aclose()
    await client.aclose()

    assert owned_http.is_closed


async def test_async_context_manager_closes_owned_http_client() -> None:
    client = MonzoClient(access_token="access-static")
    owned_http = client._transport._http_client

    async with client as entered:
        assert entered is client
        assert not owned_http.is_closed

    assert owned_http.is_closed
