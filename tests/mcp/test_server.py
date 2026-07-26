from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import httpx
import pytest

from monzo_mcp.mcp.settings import (
    AccessTokenBrokerSettings,
    AccessTokenProviderMode,
    CredentialSettings,
    HttpServerSettings,
    ServerConfigurationError,
    ServerSettings,
    access_token_provider_settings_from_environment,
)
from tests.mcp.helpers import configured_server, connected_session

if TYPE_CHECKING:
    from pathlib import Path

_READ_TOOLS = {
    "monzo_connection_status",
    "monzo_get_balance",
    "monzo_get_transaction",
    "monzo_list_accounts",
    "monzo_list_pots",
    "monzo_list_transactions",
}
_WRITE_TOOLS = {
    "monzo_annotate_transaction",
    "monzo_deposit_into_pot",
    "monzo_withdraw_from_pot",
}
_FORBIDDEN_SCHEMA_TERMS = (
    "access_token",
    "authorization_code",
    "client_secret",
    "refresh_token",
)
_ENDPOINT_TOKEN = "test-mcp-endpoint-token-" * 2
_CONTAINER_BIND = "0.0.0.0"  # noqa: S104


async def test_server_initializes_with_safe_stateless_read_only_schema(
    tmp_path: Path,
) -> None:
    server, factory, _settings = await configured_server(
        tmp_path,
        handler=lambda _request: httpx.Response(500),
    )
    try:
        async with connected_session(server) as session:
            tools_result = await session.list_tools()
    finally:
        await factory.aclose()

    assert server.settings.stateless_http is True
    assert server.settings.json_response is True
    assert {tool.name for tool in tools_result.tools} == _READ_TOOLS
    for tool in tools_result.tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.openWorldHint is True
        assert tool.outputSchema is not None
        serialized_schema = json.dumps(
            {"input": tool.inputSchema, "output": tool.outputSchema}
        ).lower()
        for term in _FORBIDDEN_SCHEMA_TERMS:
            assert term not in serialized_schema


async def test_transaction_tool_describes_monzo_recent_history_limit(
    tmp_path: Path,
) -> None:
    server, factory, _settings = await configured_server(
        tmp_path,
        handler=lambda _request: httpx.Response(500),
    )
    try:
        async with connected_session(server) as session:
            tools = (await session.list_tools()).tools
    finally:
        await factory.aclose()

    transaction_tool = next(
        tool for tool in tools if tool.name == "monzo_list_transactions"
    )
    description = transaction_tool.description or ""
    since_description = transaction_tool.inputSchema["properties"]["since"][
        "description"
    ]

    assert "90 days" in description
    assert "89-day lookback" in description
    assert "in-app verification" in description
    assert "89-day lookback" in since_description


async def test_write_tools_select_stateful_http_and_require_opt_in(
    tmp_path: Path,
) -> None:
    server, factory, _settings = await configured_server(
        tmp_path,
        handler=lambda _request: httpx.Response(500),
        enable_writes=True,
    )
    try:
        async with connected_session(server) as session:
            tools = (await session.list_tools()).tools
    finally:
        await factory.aclose()

    assert server.settings.stateless_http is False
    assert server.settings.json_response is False
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == _READ_TOOLS | _WRITE_TOOLS
    for name in _WRITE_TOOLS:
        annotations = by_name[name].annotations
        assert annotations is not None
        assert annotations.readOnlyHint is False
        assert annotations.destructiveHint is True
        assert annotations.idempotentHint is True
        assert annotations.openWorldHint is True


def test_http_settings_are_secret_safe_and_require_exact_hosts() -> None:
    settings = HttpServerSettings.from_environment(
        host=_CONTAINER_BIND,
        port=8000,
        endpoint_token=_ENDPOINT_TOKEN,
        allowed_hosts=("monzo-mcp", "monzo-mcp:8000"),
    )

    assert settings.host == _CONTAINER_BIND
    assert settings.allowed_hosts == ("monzo-mcp", "monzo-mcp:8000")
    assert settings.max_request_body_bytes == 1_048_576
    assert settings.max_concurrent_requests == 100
    assert _ENDPOINT_TOKEN not in repr(settings)


def test_http_settings_load_private_endpoint_token_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint_token_file = tmp_path / "endpoint-token"
    endpoint_token_file.write_text(_ENDPOINT_TOKEN)
    os.chmod(endpoint_token_file, 0o600)
    monkeypatch.setenv(
        "MONZO_MCP_ENDPOINT_TOKEN_FILE",
        str(endpoint_token_file),
    )

    settings = HttpServerSettings.from_environment(
        host="127.0.0.1",
    )

    assert settings.endpoint_token.get_secret_value() == _ENDPOINT_TOKEN
    assert settings.allowed_hosts == (
        "127.0.0.1",
        "127.0.0.1:8000",
        "localhost",
        "localhost:8000",
    )

    os.chmod(endpoint_token_file, 0o644)
    with pytest.raises(
        ServerConfigurationError,
        match="Invalid MCP HTTP server settings",
    ):
        HttpServerSettings.from_environment(host="127.0.0.1")


@pytest.mark.parametrize(
    ("endpoint_token", "allowed_hosts", "allowed_origins"),
    [
        ("too-short", ("monzo-mcp:8000",), ()),
        (_ENDPOINT_TOKEN, ("*.example.com",), ()),
        (_ENDPOINT_TOKEN, ("monzo-mcp:8000",), ("https://user@example.com",)),
    ],
)
def test_http_settings_reject_unsafe_configuration(
    endpoint_token: str,
    allowed_hosts: tuple[str, ...],
    allowed_origins: tuple[str, ...],
) -> None:
    with pytest.raises(ServerConfigurationError):
        HttpServerSettings.from_environment(
            host=_CONTAINER_BIND,
            endpoint_token=endpoint_token,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        )


def test_http_settings_load_bounded_resource_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONZO_MCP_HTTP_MAX_REQUEST_BODY_BYTES", "2097152")
    monkeypatch.setenv("MONZO_MCP_HTTP_MAX_CONCURRENT_REQUESTS", "50")

    settings = HttpServerSettings.from_environment(
        host="127.0.0.1",
        endpoint_token=_ENDPOINT_TOKEN,
    )

    assert settings.max_request_body_bytes == 2_097_152
    assert settings.max_concurrent_requests == 50

    monkeypatch.setenv("MONZO_MCP_HTTP_MAX_REQUEST_BODY_BYTES", "unbounded")
    with pytest.raises(
        ServerConfigurationError,
        match="Invalid MCP HTTP server settings",
    ):
        HttpServerSettings.from_environment(
            host="127.0.0.1",
            endpoint_token=_ENDPOINT_TOKEN,
        )


@pytest.mark.parametrize(
    ("max_request_body_bytes", "max_concurrent_requests"),
    [
        (1_023, 100),
        (16_777_217, 100),
        (1_048_576, 0),
        (1_048_576, 10_001),
    ],
)
def test_http_settings_reject_unbounded_resource_controls(
    max_request_body_bytes: int,
    max_concurrent_requests: int,
) -> None:
    with pytest.raises(ServerConfigurationError):
        HttpServerSettings.from_environment(
            host="127.0.0.1",
            endpoint_token=_ENDPOINT_TOKEN,
            max_request_body_bytes=max_request_body_bytes,
            max_concurrent_requests=max_concurrent_requests,
        )


def test_container_bind_requires_explicit_allowed_hosts() -> None:
    with pytest.raises(
        ServerConfigurationError,
        match="ALLOWED_HOSTS is required",
    ):
        HttpServerSettings.from_environment(
            host=_CONTAINER_BIND,
            endpoint_token=_ENDPOINT_TOKEN,
        )


def test_access_token_broker_settings_require_safe_http_url() -> None:
    settings = AccessTokenBrokerSettings.from_environment(
        url="http://api:8000/api/internal/mcp/credential-broker/v1/access-token"
    )

    assert settings.delegation_header_name == "X-MCP-Credential-Delegation"

    with pytest.raises(ServerConfigurationError):
        AccessTokenBrokerSettings.from_environment(
            url="https://user:secret@example.test/token"
        )


def test_access_token_provider_mode_defaults_to_local_and_parses_explicit_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MONZO_MCP_ACCESS_TOKEN_PROVIDER", raising=False)
    assert (
        ServerSettings.from_environment().access_token_provider
        is AccessTokenProviderMode.LOCAL
    )

    monkeypatch.setenv("MONZO_MCP_ACCESS_TOKEN_PROVIDER", "broker")
    assert (
        ServerSettings.from_environment().access_token_provider
        is AccessTokenProviderMode.BROKER
    )

    monkeypatch.setenv("MONZO_MCP_ACCESS_TOKEN_PROVIDER", "BROKER")
    with pytest.raises(
        ServerConfigurationError,
        match="must be local or broker",
    ):
        ServerSettings.from_environment()


def test_provider_settings_validate_only_the_selected_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONZO_MCP_CREDENTIAL_DIR", "relative/invalid")
    broker_settings = access_token_provider_settings_from_environment(
        ServerSettings(access_token_provider=AccessTokenProviderMode.BROKER),
        broker_url=(
            "http://api:8000/api/internal/mcp/credential-broker/v1/access-token"
        ),
    )
    assert isinstance(broker_settings, AccessTokenBrokerSettings)

    monkeypatch.delenv("MONZO_MCP_CREDENTIAL_DIR")
    local_settings = access_token_provider_settings_from_environment(
        ServerSettings(),
        credential_dir=tmp_path / "credentials",
        key_file=tmp_path / "key",
    )
    assert isinstance(local_settings, CredentialSettings)


def test_local_mode_rejects_broker_configuration_without_implicit_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MONZO_MCP_TOKEN_BROKER_URL",
        "http://api:8000/api/internal/mcp/credential-broker/v1/access-token",
    )

    with pytest.raises(
        ServerConfigurationError,
        match="MONZO_MCP_ACCESS_TOKEN_PROVIDER=broker",
    ):
        access_token_provider_settings_from_environment(ServerSettings())
