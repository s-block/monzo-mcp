"""Typed FastMCP lifespan and request context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from mcp.server.fastmcp import Context

if TYPE_CHECKING:
    import httpx
    from mcp.server.session import ServerSession
    from starlette.requests import Request

    from monzo_mcp.client import AccessTokenProvider
    from monzo_mcp.mcp.settings import AccessTokenProviderMode


@dataclass(frozen=True, slots=True)
class AppContext:
    """Resources shared by every request in one server process."""

    http_client: httpx.AsyncClient
    access_token_provider_factory: AccessTokenProviderFactory
    access_token_provider_mode: AccessTokenProviderMode
    monzo_api_base_url: str = "https://api.monzo.com"


class AccessTokenProviderFactory(Protocol):
    """Create an access-token provider for one MCP HTTP request."""

    def create(self, request: Request | None) -> AccessTokenProvider:
        """Return a provider bound to request-scoped authorization."""


# FastMCP 1.x only recognizes the concrete Context class during injection; a
# parameterized generic alias is currently treated as a user-supplied argument.
if TYPE_CHECKING:
    type MonzoMCPContext = Context[ServerSession, AppContext]
else:
    MonzoMCPContext = Context


def app_context(ctx: MonzoMCPContext) -> AppContext:
    """Return the application lifespan value with its precise local type."""
    return ctx.request_context.lifespan_context
