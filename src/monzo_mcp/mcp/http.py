"""Authenticated ASGI application and native Streamable HTTP runner."""

from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route

from monzo_mcp.mcp.runtime import open_runtime
from monzo_mcp.mcp.server import create_server
from monzo_mcp.mcp.settings import (
    AccessTokenBrokerSettings,
    AccessTokenProviderMode,
    ServerConfigurationError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from mcp.server.fastmcp import FastMCP
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp, Receive, Scope, Send

    from monzo_mcp.mcp.context import AppContext
    from monzo_mcp.mcp.settings import (
        AccessTokenProviderSettings,
        HttpServerSettings,
        ServerSettings,
    )

AUTHORIZATION_HEADER_NAME = "Authorization"
_AUTHORIZATION_HEADER_BYTES = AUTHORIZATION_HEADER_NAME.lower().encode()
_BEARER_PREFIX = b"Bearer "


class EndpointBearerMiddleware:
    """Require one exact endpoint bearer without exposing it to tool context."""

    def __init__(self, app: ASGIApp, *, expected_token: bytes) -> None:
        self._app = app
        self._expected_token = expected_token

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or scope.get("path") == "/healthz":
            await self._app(scope, receive, send)
            return
        supplied = [
            value
            for name, value in scope.get("headers", ())
            if name.lower() == _AUTHORIZATION_HEADER_BYTES
        ]
        valid = False
        if len(supplied) == 1 and supplied[0].startswith(_BEARER_PREFIX):
            valid = hmac.compare_digest(
                supplied[0][len(_BEARER_PREFIX) :],
                self._expected_token,
            )
        if not valid:
            response = PlainTextResponse("Unauthorized", status_code=401)
            await response(scope, receive, send)
            return
        inner_scope = dict(scope)
        inner_scope["headers"] = tuple(
            (name, value)
            for name, value in scope.get("headers", ())
            if name.lower() != _AUTHORIZATION_HEADER_BYTES
        )
        await self._app(inner_scope, receive, send)


async def healthcheck(_request: Request) -> Response:
    """Report only that the authenticated MCP process is accepting traffic."""
    return PlainTextResponse("ok")


def create_http_application(
    server: FastMCP[AppContext],
    settings: HttpServerSettings,
) -> Starlette:
    """Mount the official FastMCP application with process-safe HTTP controls."""

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with server.session_manager.run():
            yield

    return Starlette(
        routes=[
            Route("/healthz", healthcheck, methods=["GET"]),
            Mount("/", app=server.streamable_http_app()),
        ],
        middleware=[
            Middleware(
                EndpointBearerMiddleware,
                expected_token=settings.endpoint_token.get_secret_value().encode(),
            )
        ],
        lifespan=lifespan,
    )


async def serve_http(
    server_settings: ServerSettings,
    http_settings: HttpServerSettings,
    provider_settings: AccessTokenProviderSettings,
) -> None:
    """Run one authenticated HTTP server with the selected provider lifecycle."""
    provider_mode = (
        AccessTokenProviderMode.BROKER
        if isinstance(provider_settings, AccessTokenBrokerSettings)
        else AccessTokenProviderMode.LOCAL
    )
    if server_settings.access_token_provider is not provider_mode:
        raise ServerConfigurationError(
            "MCP server and access-token provider modes do not match"
        )
    async with open_runtime(provider_settings) as app_context:
        mcp_server = create_server(
            server_settings,
            http_settings,
            app_context=app_context,
        )
        application = create_http_application(mcp_server, http_settings)
        config = uvicorn.Config(
            application,
            host=http_settings.host,
            port=http_settings.port,
            log_level="warning",
            access_log=False,
            proxy_headers=False,
            server_header=False,
        )
        await uvicorn.Server(config).serve()


def run_server(
    server_settings: ServerSettings,
    http_settings: HttpServerSettings,
    provider_settings: AccessTokenProviderSettings,
) -> None:
    """Run the container's only MCP transport."""
    asyncio.run(serve_http(server_settings, http_settings, provider_settings))
