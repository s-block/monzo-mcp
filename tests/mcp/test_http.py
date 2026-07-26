"""Authenticated native Streamable HTTP service tests."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
from aiomonzo import OAuthClientConfig, OAuthToken
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyHttpUrl, SecretStr

from monzo_mcp.credentials import ClientCredentialStore
from monzo_mcp.mcp.http import (
    AUTHORIZATION_HEADER_NAME,
    create_http_application,
)
from monzo_mcp.mcp.runtime import open_runtime
from monzo_mcp.mcp.server import create_server
from monzo_mcp.mcp.settings import (
    AccessTokenBrokerSettings,
    AccessTokenProviderMode,
    CredentialSettings,
    HttpServerSettings,
    ServerSettings,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    import pytest
    from mcp.server.fastmcp import FastMCP
    from starlette.applications import Starlette

    from monzo_mcp.mcp.context import AppContext

_ENDPOINT_TOKEN = "test-local-endpoint-token-" * 2
_DELEGATION = "Bearer test-short-lived-delegation"
_BASE_URL = "http://monzo-mcp:8000"
_RESOURCE_URL = f"{_BASE_URL}/mcp"
_BROKER_URL = "https://credential-broker.test/access-token"


def _http_settings() -> HttpServerSettings:
    return HttpServerSettings(
        host="127.0.0.1",
        port=8000,
        endpoint_token=SecretStr(_ENDPOINT_TOKEN),
        allowed_hosts=("monzo-mcp", "monzo-mcp:8000"),
    )


@asynccontextmanager
async def _running_application(
    *,
    handler: Callable[[httpx.Request], httpx.Response],
) -> AsyncIterator[tuple[FastMCP[AppContext], Starlette]]:
    http_settings = _http_settings()
    broker_settings = AccessTokenBrokerSettings(url=_BROKER_URL)
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream_client,
        open_runtime(
            broker_settings,
            http_client=upstream_client,
            monzo_api_base_url="https://api.monzo.test",
        ) as app_context,
    ):
        server = create_server(
            ServerSettings(
                access_token_provider=AccessTokenProviderMode.BROKER,
            ),
            http_settings,
            app_context=app_context,
        )
        application = create_http_application(server, http_settings)
        async with application.router.lifespan_context(application):
            yield server, application


@asynccontextmanager
async def _connected_http_session(
    *,
    application: Starlette,
    include_delegation: bool = True,
) -> AsyncIterator[ClientSession]:
    headers = {AUTHORIZATION_HEADER_NAME: f"Bearer {_ENDPOINT_TOKEN}"}
    if include_delegation:
        headers["X-MCP-Credential-Delegation"] = _DELEGATION
    transport = httpx.ASGITransport(app=application)
    async with (
        httpx.AsyncClient(
            transport=transport,
            base_url=_BASE_URL,
            headers=headers,
        ) as http_client,
        streamable_http_client(
            _RESOURCE_URL,
            http_client=http_client,
        ) as (read_stream, write_stream, _session_id),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        yield session


async def test_http_transport_requires_exact_bearer_and_safe_origin(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with _running_application(handler=lambda _request: httpx.Response(500)) as (
        _server,
        application,
    ):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url=_BASE_URL,
        ) as client:
            health = await client.get("/healthz")
            missing = await client.get(
                "/mcp",
                headers={"Accept": "text/event-stream"},
            )
            wrong = await client.get(
                "/mcp",
                headers={
                    "Accept": "text/event-stream",
                    AUTHORIZATION_HEADER_NAME: "Bearer wrong-test-token",
                },
            )
            duplicate = await client.get(
                "/mcp",
                headers=[
                    ("Accept", "text/event-stream"),
                    (
                        AUTHORIZATION_HEADER_NAME,
                        f"Bearer {_ENDPOINT_TOKEN}",
                    ),
                    (
                        AUTHORIZATION_HEADER_NAME,
                        f"Bearer {_ENDPOINT_TOKEN}",
                    ),
                ],
            )
            disallowed_origin = await client.get(
                "/mcp",
                headers={
                    "Accept": "text/event-stream",
                    AUTHORIZATION_HEADER_NAME: f"Bearer {_ENDPOINT_TOKEN}",
                    "Origin": "https://attacker.example",
                },
            )

    assert health.status_code == 200
    assert health.text == "ok"
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert duplicate.status_code == 401
    assert disallowed_origin.status_code == 403
    serialized = f"{missing.text}{wrong.text}{duplicate.text}{disallowed_origin.text}"
    assert _ENDPOINT_TOKEN not in serialized
    assert caplog.messages.count("security_event=endpoint_authentication_failed") == 3
    assert "security_event=transport_policy_rejected" in caplog.messages
    assert _ENDPOINT_TOKEN not in caplog.text


async def test_http_transport_rejects_fixed_and_streamed_oversized_bodies(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def streamed_body() -> AsyncIterator[bytes]:
        yield b"x" * 700_000
        yield b"x" * 700_000

    async with _running_application(handler=lambda _request: httpx.Response(500)) as (
        _server,
        application,
    ):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(
            transport=transport,
            base_url=_BASE_URL,
            headers={
                AUTHORIZATION_HEADER_NAME: f"Bearer {_ENDPOINT_TOKEN}",
            },
        ) as client:
            fixed = await client.post("/mcp", content=b"x" * 1_048_577)
            streamed = await client.post("/mcp", content=streamed_body())

    assert fixed.status_code == 413
    assert streamed.status_code == 413
    assert fixed.text == "Request body too large"
    assert streamed.text == "Request body too large"
    assert caplog.messages.count("security_event=request_body_too_large") == 2
    assert _ENDPOINT_TOKEN not in caplog.text


async def test_http_transport_supports_concurrent_stateless_clients() -> None:
    clients_ready = 0
    both_clients_ready = asyncio.Event()

    async def list_tool_names(application: Starlette) -> set[str]:
        nonlocal clients_ready
        async with _connected_http_session(application=application) as session:
            clients_ready += 1
            if clients_ready == 2:
                both_clients_ready.set()
            await asyncio.wait_for(both_clients_ready.wait(), timeout=2)
            return {tool.name for tool in (await session.list_tools()).tools}

    async with _running_application(handler=lambda _request: httpx.Response(500)) as (
        server,
        application,
    ):
        first, second = await asyncio.gather(
            list_tool_names(application),
            list_tool_names(application),
        )

    assert server.settings.stateless_http is True
    assert first == second
    assert "monzo_connection_status" in first


async def test_endpoint_and_delegated_bearers_are_kept_on_separate_hops() -> None:
    broker_requests = 0
    monzo_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal broker_requests, monzo_requests
        assert _ENDPOINT_TOKEN not in request.headers.get("Authorization", "")
        if str(request.url) == _BROKER_URL:
            broker_requests += 1
            assert request.headers["Authorization"] == _DELEGATION
            assert "X-MCP-Credential-Delegation" not in request.headers
            return httpx.Response(
                200,
                json={
                    "access_token": "test-provider-access-token",
                    "token_type": "Bearer",
                    "expires_at": None,
                },
            )
        monzo_requests += 1
        assert request.url.host == "api.monzo.test"
        assert request.headers["Authorization"] == ("Bearer test-provider-access-token")
        assert "X-MCP-Credential-Delegation" not in request.headers
        return httpx.Response(200, json={"authenticated": True})

    async with (
        _running_application(handler=handler) as (_server, application),
        _connected_http_session(application=application) as session,
    ):
        result = await session.call_tool("monzo_connection_status")

    assert result.structuredContent == {"authenticated": True}
    assert broker_requests == 1
    assert monzo_requests == 1


async def test_local_mode_needs_no_delegation_and_keeps_endpoint_bearer_separate(
    tmp_path: Path,
) -> None:
    credential_settings = CredentialSettings(
        credential_dir=tmp_path / "credentials",
        key_file=tmp_path / "key",
    )
    async with ClientCredentialStore(
        credential_dir=credential_settings.credential_dir,
        key_file=credential_settings.key_file,
        create_key=True,
    ) as store:
        await store.save_oauth(
            OAuthClientConfig(
                client_id="client_1",
                client_secret=SecretStr("client-sensitive"),
                redirect_uri=AnyHttpUrl("http://127.0.0.1:8765/oauth/callback"),
            )
        )
        await store.save(
            OAuthToken(
                access_token=SecretStr("test-local-monzo-token"),
                refresh_token=SecretStr("test-local-refresh-token"),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                client_id="client_1",
            )
        )

    monzo_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal monzo_requests
        monzo_requests += 1
        assert request.url.host == "api.monzo.test"
        assert request.headers["Authorization"] == "Bearer test-local-monzo-token"
        assert _ENDPOINT_TOKEN not in request.headers["Authorization"]
        assert "X-MCP-Credential-Delegation" not in request.headers
        return httpx.Response(200, json={"authenticated": True})

    http_settings = _http_settings()
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream_client,
        open_runtime(
            credential_settings,
            http_client=upstream_client,
            monzo_api_base_url="https://api.monzo.test",
        ) as app_context,
    ):
        server = create_server(
            ServerSettings(),
            http_settings,
            app_context=app_context,
        )
        application = create_http_application(server, http_settings)
        async with (
            application.router.lifespan_context(application),
            _connected_http_session(
                application=application,
                include_delegation=False,
            ) as session,
        ):
            result = await session.call_tool("monzo_connection_status")

    assert result.structuredContent == {"authenticated": True}
    assert monzo_requests == 1


async def test_tool_call_without_delegation_fails_without_contacting_upstream(
    caplog: pytest.LogCaptureFixture,
) -> None:
    upstream_requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal upstream_requests
        upstream_requests += 1
        return httpx.Response(500)

    async with (
        _running_application(handler=handler) as (_server, application),
        _connected_http_session(
            application=application,
            include_delegation=False,
        ) as session,
    ):
        result = await session.call_tool("monzo_connection_status")

    assert result.isError is True
    assert upstream_requests == 0
    assert "security_event=credential_broker_authorization_failed" in caplog.messages
    assert _DELEGATION not in caplog.text
