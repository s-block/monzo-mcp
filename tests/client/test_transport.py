"""Authentication replay, retry, failure mapping, and lifecycle tests."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import AnyHttpUrl, SecretStr

from monzo_mcp.client import (
    InMemoryTokenStore,
    MonzoAuthenticationError,
    MonzoClient,
    MonzoClosedError,
    MonzoHTTPError,
    MonzoPermissionError,
    MonzoRateLimitError,
    MonzoResponseDecodeError,
    MonzoResponseValidationError,
    MonzoTimeoutError,
    MonzoTransportError,
    OAuthClientConfig,
    OAuthToken,
    RetryPolicy,
)
from tests.client.helpers import POT, TOKEN


def _oauth_config() -> OAuthClientConfig:
    return OAuthClientConfig(
        client_id="client_1",
        client_secret=SecretStr("client-sensitive"),
        redirect_uri=AnyHttpUrl("https://client.example/callback"),
    )


def _zero_delay_policy(*, max_attempts: int = 3) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=max_attempts,
        base_delay_seconds=0.0,
        max_delay_seconds=0.0,
        max_elapsed_seconds=0.0,
        jitter_ratio=0.0,
    )


async def test_invalid_token_refreshes_and_replays_request_once() -> None:
    current = OAuthToken(
        access_token=SecretStr("access-old"),
        refresh_token=SecretStr("refresh-old"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, request.headers.get("authorization", "")))
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, content=TOKEN)
        if request.headers["authorization"] == "Bearer access-old":
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(200, json={"authenticated": True})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(
        oauth=_oauth_config(),
        token_store=InMemoryTokenStore(current),
        http_client=http,
    )

    identity = await client.who_am_i()

    assert identity.authenticated
    assert calls == [
        ("/ping/whoami", "Bearer access-old"),
        ("/oauth2/token", ""),
        ("/ping/whoami", "Bearer access-new"),
    ]
    await client.aclose()
    await http.aclose()


async def test_repeated_invalid_token_is_not_an_infinite_refresh_loop() -> None:
    current = OAuthToken(
        access_token=SecretStr("access-old"),
        refresh_token=SecretStr("refresh-old"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    api_calls = 0
    refreshes = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_calls, refreshes
        if request.url.path == "/oauth2/token":
            refreshes += 1
            return httpx.Response(200, content=TOKEN)
        api_calls += 1
        return httpx.Response(401, json={"error": "invalid_token"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(
        oauth=_oauth_config(),
        token_store=InMemoryTokenStore(current),
        http_client=http,
    )

    with pytest.raises(MonzoAuthenticationError):
        await client.who_am_i()

    assert refreshes == 1
    assert api_calls == 2
    await client.aclose()
    await http.aclose()


async def test_safe_read_retries_transient_status_to_configured_cap() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert request.headers["user-agent"] == "monzo-mcp/0.1.0"
        if requests < 3:
            return httpx.Response(503, json={"message": "Temporarily unavailable"})
        return httpx.Response(200, json={"authenticated": True})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(
        access_token="access-static",
        http_client=http,
        retry_policy=_zero_delay_policy(),
    )

    identity = await client.who_am_i()

    assert identity.authenticated
    assert requests == 3
    await client.aclose()
    await http.aclose()


async def test_pot_retry_reuses_exact_dedupe_id_and_form() -> None:
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        if len(bodies) == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=POT)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(
        access_token="access-static",
        http_client=http,
        retry_policy=_zero_delay_policy(max_attempts=2),
    )

    pot = await client.deposit_into_pot(
        "pot_1",
        source_account_id="acc_1",
        amount=100,
        dedupe_id="stable-operation-id",
    )

    assert pot.id == "pot_1"
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]
    assert b"dedupe_id=stable-operation-id" in bodies[0]
    await client.aclose()
    await http.aclose()


async def test_non_idempotent_annotation_is_not_retried() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(503)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(
        access_token="access-static",
        http_client=http,
        retry_policy=_zero_delay_policy(),
    )

    with pytest.raises(MonzoHTTPError):
        await client.annotate_transaction("tx_1", {"key": "value"})

    assert requests == 1
    await client.aclose()
    await http.aclose()


async def test_retry_after_beyond_policy_is_not_retried_early() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(429, headers={"Retry-After": "60"})

    policy = RetryPolicy(
        max_attempts=3,
        base_delay_seconds=0.0,
        max_delay_seconds=1.0,
        max_elapsed_seconds=5.0,
        jitter_ratio=0.0,
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(
        access_token="access-static", http_client=http, retry_policy=policy
    )

    with pytest.raises(MonzoRateLimitError) as captured:
        await client.who_am_i()

    assert captured.value.retry_after == 60.0
    assert requests == 1
    await client.aclose()
    await http.aclose()


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (401, {"message": "Unauthorized"}, MonzoAuthenticationError),
        (403, {}, MonzoPermissionError),
        (429, {"message": "Slow down"}, MonzoRateLimitError),
        (500, {"code": "internal", "message": "Failed"}, MonzoHTTPError),
    ],
)
async def test_http_error_mapping(
    status: int,
    payload: dict[str, str],
    expected: type[MonzoHTTPError],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload, headers={"X-Request-ID": "req_1"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(
        access_token="access-static",
        http_client=http,
        retry_policy=_zero_delay_policy(max_attempts=1),
    )

    with pytest.raises(expected) as captured:
        await client.who_am_i()

    assert captured.value.status_code == status
    assert captured.value.request_id == "req_1"
    await client.aclose()
    await http.aclose()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"not-json", MonzoResponseDecodeError),
        (b'{"authenticated":"yes"}', MonzoResponseValidationError),
    ],
)
async def test_success_response_decode_and_schema_failures(
    content: bytes,
    expected: type[Exception],
) -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))
    )
    client = MonzoClient(access_token="access-static", http_client=http)

    with pytest.raises(expected):
        await client.who_am_i()

    await client.aclose()
    await http.aclose()


async def test_timeout_is_typed_and_safe_read_retries() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise httpx.ReadTimeout("contains-internal-url", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(
        access_token="access-sensitive",
        http_client=http,
        retry_policy=_zero_delay_policy(max_attempts=2),
    )

    with pytest.raises(MonzoTimeoutError) as captured:
        await client.who_am_i()

    assert requests == 2
    assert "access-sensitive" not in str(captured.value)
    assert "internal-url" not in str(captured.value)
    await client.aclose()
    await http.aclose()


async def test_connection_failure_is_typed_and_safe_read_retries() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise httpx.ConnectError("network details", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(
        access_token="access-static",
        http_client=http,
        retry_policy=_zero_delay_policy(max_attempts=2),
    )

    with pytest.raises(MonzoTransportError) as captured:
        await client.who_am_i()

    assert str(captured.value) == "Monzo API request failed"
    assert requests == 2
    await client.aclose()
    await http.aclose()


async def test_cancellation_propagates_without_retry() -> None:
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise asyncio.CancelledError

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = MonzoClient(
        access_token="access-static",
        http_client=http,
        retry_policy=_zero_delay_policy(),
    )

    with pytest.raises(asyncio.CancelledError):
        await client.who_am_i()

    assert requests == 1
    await client.aclose()
    await http.aclose()


async def test_injected_http_client_is_not_closed_but_client_rejects_reuse() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"authenticated": True})
        )
    )
    client = MonzoClient(access_token="access-static", http_client=http)

    await client.aclose()

    assert not http.is_closed
    with pytest.raises(MonzoClosedError):
        await client.who_am_i()
    await http.aclose()
