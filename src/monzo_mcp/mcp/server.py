"""FastMCP composition for the HTTP-only Monzo service."""

from __future__ import annotations

from collections.abc import AsyncIterator  # noqa: TC003
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from monzo_mcp.mcp.context import AppContext
from monzo_mcp.mcp.settings import AccessTokenProviderMode
from monzo_mcp.mcp.tools import register_tools

if TYPE_CHECKING:
    from monzo_mcp.mcp.settings import HttpServerSettings, ServerSettings

_COMMON_INSTRUCTIONS = """
Use explicit account, pot, and transaction identifiers; never guess a financial
target. All money values are integer minor currency units (100 is £1.00 for GBP).
Transaction calls return one bounded page with at most 100 entries. If Monzo
reports insufficient permission after login, the human must approve access in the
Monzo mobile app. Mutation tools are absent unless enabled at server startup, and
pot movements additionally require immediate client-side human confirmation.
""".strip()
_LOCAL_CREDENTIAL_INSTRUCTIONS = """
Monzo credentials are loaded from the operator-owned encrypted credential bundle.
They are never accepted as tool arguments or returned by this server.
""".strip()
_BROKER_CREDENTIAL_INSTRUCTIONS = """
A short-lived credential delegation is supplied by the MCP host to an external
access-token broker. Provider credentials are never accepted as tool arguments or
stored by this server.
""".strip()


def create_server(
    settings: ServerSettings,
    http_settings: HttpServerSettings,
    *,
    app_context: AppContext,
) -> FastMCP[AppContext]:
    """Create the HTTP MCP server over an already-open process runtime."""

    @asynccontextmanager
    async def lifespan(
        _server: FastMCP[AppContext],
    ) -> AsyncIterator[AppContext]:
        yield app_context

    stateful = settings.enable_writes
    credential_instructions = (
        _BROKER_CREDENTIAL_INSTRUCTIONS
        if settings.access_token_provider is AccessTokenProviderMode.BROKER
        else _LOCAL_CREDENTIAL_INSTRUCTIONS
    )
    server = FastMCP[AppContext](
        name="monzo-mcp",
        instructions=f"{_COMMON_INSTRUCTIONS}\n{credential_instructions}",
        json_response=not stateful,
        stateless_http=not stateful,
        log_level="WARNING",
        lifespan=lifespan,
        host=http_settings.host,
        port=http_settings.port,
        streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(http_settings.allowed_hosts),
            allowed_origins=list(http_settings.allowed_origins),
        ),
    )
    register_tools(server, enable_writes=settings.enable_writes)
    return server
