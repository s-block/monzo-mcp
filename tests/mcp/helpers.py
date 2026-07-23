"""Test helpers for connected in-memory MCP sessions."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import SecretStr

from monzo_mcp.client import AccessTokenProvider
from monzo_mcp.mcp.context import AccessTokenProviderFactory, AppContext
from monzo_mcp.mcp.server import create_server
from monzo_mcp.mcp.settings import (
    AccessTokenProviderMode,
    HttpServerSettings,
    ServerSettings,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    from mcp import ClientSession
    from mcp.client.session import ElicitationFnT
    from mcp.server.fastmcp import FastMCP
    from starlette.requests import Request


@dataclass(slots=True)
class StaticAccessTokenProvider(AccessTokenProvider):
    """Return a deterministic request-lived provider token."""

    access_token: str = "access-static"
    gets: int = 0
    refreshes: int = 0

    async def get_access_token(self) -> str:
        self.gets += 1
        return self.access_token

    async def refresh_after_rejection(self, rejected_access_token: str) -> str:
        self.refreshes += 1
        return self.access_token


@dataclass(slots=True)
class StaticAccessTokenProviderFactory(AccessTokenProviderFactory):
    """Create deterministic providers for HTTP and memory MCP tests."""

    provider: StaticAccessTokenProvider
    created: int = 0

    def create(self, request: Request | None) -> AccessTokenProvider:
        del request
        self.created += 1
        return self.provider


@dataclass(slots=True)
class MockRuntime:
    """Own the pooled mock transport and observable provider factory."""

    http_client: httpx.AsyncClient
    provider_factory: StaticAccessTokenProviderFactory

    async def aclose(self) -> None:
        await self.http_client.aclose()


async def configured_server(
    tmp_path: Path,
    *,
    handler: Callable[[httpx.Request], httpx.Response],
    enable_writes: bool = False,
    access_token_provider: AccessTokenProviderMode = AccessTokenProviderMode.LOCAL,
) -> tuple[FastMCP[AppContext], MockRuntime, ServerSettings]:
    """Return a server with a deterministic request-scoped provider."""
    del tmp_path
    settings = ServerSettings(
        enable_writes=enable_writes,
        access_token_provider=access_token_provider,
    )
    provider_factory = StaticAccessTokenProviderFactory(
        provider=StaticAccessTokenProvider()
    )
    runtime = MockRuntime(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        provider_factory=provider_factory,
    )
    http_settings = HttpServerSettings(
        host="127.0.0.1",
        port=8000,
        endpoint_token=SecretStr("test-mcp-endpoint-token-" * 2),
        allowed_hosts=("testserver",),
    )
    return (
        create_server(
            settings,
            http_settings,
            app_context=AppContext(
                http_client=runtime.http_client,
                access_token_provider_factory=provider_factory,
                access_token_provider_mode=settings.access_token_provider,
                monzo_api_base_url="https://api.monzo.test",
            ),
        ),
        runtime,
        settings,
    )


@asynccontextmanager
async def connected_session(
    server: FastMCP[AppContext],
    *,
    elicitation_callback: ElicitationFnT | None = None,
) -> AsyncIterator[ClientSession]:
    """Connect an official MCP client session to a FastMCP server in memory."""
    async with create_connected_server_and_client_session(
        server,
        raise_exceptions=True,
        elicitation_callback=elicitation_callback,
    ) as session:
        yield session
