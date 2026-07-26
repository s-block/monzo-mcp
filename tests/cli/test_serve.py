"""CLI HTTP service selection tests."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

import monzo_mcp.cli as cli
from monzo_mcp.mcp.settings import (
    AccessTokenBrokerSettings,
    AccessTokenProviderSettings,
    CredentialSettings,
    HttpServerSettings,
    ServerSettings,
)

if TYPE_CHECKING:
    from pathlib import Path

_ENDPOINT_TOKEN = "test-local-endpoint-token-" * 2
_CONTAINER_BIND = "0.0.0.0"  # noqa: S104
_BROKER_URL = "http://api:8000/api/internal/mcp/credential-broker/v1/access-token"


def test_serve_builds_authenticated_http_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[
        tuple[ServerSettings, HttpServerSettings, AccessTokenProviderSettings]
    ] = []
    endpoint_token_file = tmp_path / "endpoint-token"
    endpoint_token_file.write_text(_ENDPOINT_TOKEN)
    os.chmod(endpoint_token_file, 0o600)

    def capture(
        settings: ServerSettings,
        http_settings: HttpServerSettings,
        provider_settings: AccessTokenProviderSettings,
    ) -> None:
        calls.append((settings, http_settings, provider_settings))

    monkeypatch.setattr(cli, "run_server", capture)
    monkeypatch.setenv("MONZO_MCP_ACCESS_TOKEN_PROVIDER", "broker")

    cli.main(
        [
            "serve",
            "--host",
            _CONTAINER_BIND,
            "--port",
            "8000",
            "--endpoint-token-file",
            str(endpoint_token_file),
            "--token-broker-url",
            _BROKER_URL,
            "--allowed-host",
            "monzo-mcp:8000",
        ]
    )

    assert len(calls) == 1
    settings, http_settings, provider_settings = calls[0]
    assert settings.enable_writes is False
    assert http_settings.host == _CONTAINER_BIND
    assert http_settings.allowed_hosts == ("monzo-mcp:8000",)
    assert http_settings.endpoint_token.get_secret_value() == _ENDPOINT_TOKEN
    assert isinstance(provider_settings, AccessTokenBrokerSettings)
    assert provider_settings.url == _BROKER_URL


def test_serve_defaults_to_local_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[
        tuple[ServerSettings, HttpServerSettings, AccessTokenProviderSettings]
    ] = []
    endpoint_token_file = tmp_path / "endpoint-token"
    endpoint_token_file.write_text(_ENDPOINT_TOKEN)
    os.chmod(endpoint_token_file, 0o600)
    credential_dir = tmp_path / "credentials"
    key_file = tmp_path / "key"

    def capture(
        settings: ServerSettings,
        http_settings: HttpServerSettings,
        provider_settings: AccessTokenProviderSettings,
    ) -> None:
        calls.append((settings, http_settings, provider_settings))

    monkeypatch.setattr(cli, "run_server", capture)
    monkeypatch.delenv("MONZO_MCP_ACCESS_TOKEN_PROVIDER", raising=False)
    monkeypatch.delenv("MONZO_MCP_TOKEN_BROKER_URL", raising=False)

    cli.main(
        [
            "serve",
            "--host",
            "127.0.0.1",
            "--endpoint-token-file",
            str(endpoint_token_file),
            "--credential-dir",
            str(credential_dir),
            "--key-file",
            str(key_file),
        ]
    )

    assert len(calls) == 1
    settings, _http_settings, provider_settings = calls[0]
    assert settings.access_token_provider.value == "local"
    assert isinstance(provider_settings, CredentialSettings)
    assert provider_settings.credential_dir == credential_dir
    assert provider_settings.key_file == key_file


def test_serve_rejects_missing_endpoint_token_file(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(
        "MONZO_MCP_ENDPOINT_TOKEN_FILE",
        "/missing/monzo-mcp-endpoint-token",
    )
    monkeypatch.setenv("MONZO_MCP_HTTP_ALLOWED_HOSTS", "monzo-mcp:8000")
    monkeypatch.setenv("MONZO_MCP_TOKEN_BROKER_URL", _BROKER_URL)

    with pytest.raises(SystemExit) as raised:
        cli.main(["serve"])

    assert raised.value.code == 2
    assert "Invalid MCP HTTP server settings" in capsys.readouterr().err


def test_serve_rejects_broker_url_without_explicit_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    endpoint_token_file = tmp_path / "endpoint-token"
    endpoint_token_file.write_text(_ENDPOINT_TOKEN)
    os.chmod(endpoint_token_file, 0o600)
    monkeypatch.delenv("MONZO_MCP_ACCESS_TOKEN_PROVIDER", raising=False)

    with pytest.raises(SystemExit) as raised:
        cli.main(
            [
                "serve",
                "--host",
                "127.0.0.1",
                "--endpoint-token-file",
                str(endpoint_token_file),
                "--token-broker-url",
                _BROKER_URL,
            ]
        )

    assert raised.value.code == 2
    error = capsys.readouterr().err
    assert "MONZO_MCP_ACCESS_TOKEN_PROVIDER=broker" in error
    assert _ENDPOINT_TOKEN not in error


def test_serve_http_compatibility_command_is_removed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["serve-http"])

    assert raised.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
