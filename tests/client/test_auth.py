"""OAuth flow and refresh-concurrency tests."""

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import AnyHttpUrl, SecretStr

from monzo_mcp.client import (
    InMemoryTokenStore,
    MonzoAuthenticationError,
    MonzoClient,
    MonzoConfigurationError,
    MonzoReauthenticationRequired,
    MonzoResponseDecodeError,
    MonzoResponseValidationError,
    OAuthClientConfig,
    OAuthToken,
    validate_oauth_state,
)
from tests.client.helpers import TOKEN


def _oauth_config() -> OAuthClientConfig:
    return OAuthClientConfig(
        client_id="client_1",
        client_secret=SecretStr("client-sensitive"),
        redirect_uri=AnyHttpUrl("https://client.example/callback"),
    )


async def test_authorization_request_contains_encoded_config_and_state() -> None:
    client = MonzoClient(oauth=_oauth_config())

    authorization = client.create_authorization_request(state="state-value")
    query = parse_qs(urlparse(str(authorization.url)).query)

    assert query == {
        "client_id": ["client_1"],
        "redirect_uri": ["https://client.example/callback"],
        "response_type": ["code"],
        "state": ["state-value"],
    }
    assert authorization.state == "state-value"
    await client.aclose()


def test_state_validation_rejects_empty_or_mismatched_values() -> None:
    validate_oauth_state(expected="same", returned="same")

    with pytest.raises(MonzoConfigurationError):
        validate_oauth_state(expected="expected", returned="different")
    with pytest.raises(MonzoConfigurationError):
        validate_oauth_state(expected="", returned="")


async def test_authorization_request_rejects_explicit_empty_state() -> None:
    client = MonzoClient(oauth=_oauth_config())

    with pytest.raises(MonzoConfigurationError):
        client.create_authorization_request(state="")

    await client.aclose()


async def test_code_exchange_sends_exact_form_and_persists_token() -> None:
    store = InMemoryTokenStore()
    received_form: dict[str, list[str]] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal received_form
        received_form = parse_qs(request.content.decode())
        assert request.method == "POST"
        assert request.url.path == "/oauth2/token"
        return httpx.Response(200, content=TOKEN)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(oauth=_oauth_config(), token_store=store, http_client=http)

    token = await client.exchange_authorization_code(
        "authorization-sensitive",
        expected_state="state-value",
        returned_state="state-value",
    )

    assert received_form == {
        "grant_type": ["authorization_code"],
        "client_id": ["client_1"],
        "client_secret": ["client-sensitive"],
        "redirect_uri": ["https://client.example/callback"],
        "code": ["authorization-sensitive"],
    }
    assert (await store.load()) == token
    assert token.refresh_token is not None
    assert token.refresh_token.get_secret_value() == "refresh-new"
    await client.aclose()
    await http.aclose()


async def test_state_mismatch_never_sends_authorization_code() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(oauth=_oauth_config(), http_client=http)

    with pytest.raises(MonzoConfigurationError):
        await client.exchange_authorization_code(
            "authorization-sensitive",
            expected_state="expected",
            returned_state="returned",
        )

    assert requests == 0
    await client.aclose()
    await http.aclose()


async def test_expired_token_refreshes_and_rotates_stored_refresh_token() -> None:
    old = OAuthToken(
        access_token=SecretStr("access-old"),
        refresh_token=SecretStr("refresh-old"),
        expires_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    store = InMemoryTokenStore(old)
    forms: list[dict[str, list[str]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            forms.append(parse_qs(request.content.decode()))
            return httpx.Response(200, content=TOKEN)
        assert request.headers["authorization"] == "Bearer access-new"
        return httpx.Response(
            200,
            json={
                "authenticated": True,
                "client_id": "client_1",
                "user_id": "user_1",
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(oauth=_oauth_config(), token_store=store, http_client=http)

    identity = await client.who_am_i()

    assert identity.authenticated
    assert forms == [
        {
            "grant_type": ["refresh_token"],
            "client_id": ["client_1"],
            "client_secret": ["client-sensitive"],
            "refresh_token": ["refresh-old"],
        }
    ]
    stored = await store.load()
    assert stored is not None
    assert stored.refresh_token is not None
    assert stored.refresh_token.get_secret_value() == "refresh-new"
    await client.aclose()
    await http.aclose()


async def test_concurrent_expired_requests_perform_exactly_one_refresh() -> None:
    old = OAuthToken(
        access_token=SecretStr("access-old"),
        refresh_token=SecretStr("refresh-old"),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    store = InMemoryTokenStore(old)
    refreshes = 0
    api_tokens: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refreshes
        if request.url.path == "/oauth2/token":
            refreshes += 1
            await asyncio.sleep(0)
            return httpx.Response(200, content=TOKEN)
        api_tokens.append(request.headers["authorization"])
        return httpx.Response(200, json={"authenticated": True})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(oauth=_oauth_config(), token_store=store, http_client=http)

    results = await asyncio.gather(*(client.who_am_i() for _ in range(20)))

    assert refreshes == 1
    assert all(result.authenticated for result in results)
    assert api_tokens == ["Bearer access-new"] * 20
    await client.aclose()
    await http.aclose()


async def test_expired_static_token_requires_new_authorization() -> None:
    token = OAuthToken(
        access_token=SecretStr("access-sensitive"),
        expires_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500))
    )
    client = MonzoClient(access_token=token, http_client=http)

    with pytest.raises(MonzoReauthenticationRequired):
        await client.who_am_i()

    await client.aclose()
    await http.aclose()


async def test_refresh_failure_is_typed_and_does_not_expose_secrets() -> None:
    old = OAuthToken(
        access_token=SecretStr("access-sensitive"),
        refresh_token=SecretStr("refresh-sensitive"),
        expires_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": "invalid_grant", "message": "Grant was rejected"},
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(
        oauth=_oauth_config(),
        token_store=InMemoryTokenStore(old),
        http_client=http,
    )

    with pytest.raises(MonzoAuthenticationError) as captured:
        await client.refresh_access_token()

    rendered = str(captured.value)
    assert "access-sensitive" not in rendered
    assert "refresh-sensitive" not in rendered
    assert "client-sensitive" not in rendered
    await client.aclose()
    await http.aclose()


async def test_refresh_requires_and_does_not_lose_rotated_refresh_token() -> None:
    old = OAuthToken(
        access_token=SecretStr("access-old"),
        refresh_token=SecretStr("refresh-old"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    store = InMemoryTokenStore(old)
    response_without_refresh = b"""{
      "access_token": "access-new",
      "expires_in": 21600,
      "token_type": "Bearer"
    }"""
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=response_without_refresh)
        )
    )
    client = MonzoClient(oauth=_oauth_config(), token_store=store, http_client=http)

    with pytest.raises(MonzoResponseValidationError):
        await client.refresh_access_token()

    assert await store.load() == old
    await client.aclose()
    await http.aclose()


async def test_invalid_oauth_json_is_a_decode_error() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"not-json")
        )
    )
    client = MonzoClient(oauth=_oauth_config(), http_client=http)

    with pytest.raises(MonzoResponseDecodeError):
        await client.exchange_authorization_code(
            "code",
            expected_state="state",
            returned_state="state",
        )

    await client.aclose()
    await http.aclose()
