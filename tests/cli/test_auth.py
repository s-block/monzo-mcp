"""Human OAuth helper and loopback callback tests."""

from __future__ import annotations

import asyncio
import os
import socket
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Self

import pytest
from aiomonzo import (
    AuthorizationRequest,
    MonzoConfigurationError,
    OAuthClientConfig,
    OAuthToken,
    TokenStore,
    validate_oauth_state,
)
from pydantic import AnyHttpUrl, SecretStr

import monzo_mcp.cli as cli
from monzo_mcp.credentials import ClientCredentialStore
from monzo_mcp.mcp.settings import CredentialSettings

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

_CLIENT_SECRET = "client-sensitive-secret"
_AUTHORIZATION_CODE = "authorization-sensitive-code"
_ACCESS_TOKEN = "access-sensitive-token"
_REFRESH_TOKEN = "refresh-sensitive-token"
_STATE = "state-sensitive-value"


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        address = candidate.getsockname()
    assert isinstance(address, tuple)
    port = address[1]
    assert isinstance(port, int)
    return port


async def _send_callback(port: int, target: str) -> str:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
        await writer.drain()
        return (await reader.read()).decode()
    finally:
        writer.close()
        await writer.wait_closed()


async def _send_when_ready(port: int, target: str) -> str:
    for _ in range(100):
        try:
            return await _send_callback(port, target)
        except OSError:
            await asyncio.sleep(0.001)
    raise AssertionError("OAuth callback listener did not start")


async def test_loopback_callback_is_one_shot_and_secret_safe() -> None:
    port = _available_loopback_port()
    redirect_uri = AnyHttpUrl(f"http://127.0.0.1:{port}/oauth/callback")
    listener = cli._LoopbackCallbackServer(
        redirect_uri=redirect_uri,
        bind_host=None,
    )

    async with listener:
        wait = asyncio.create_task(listener.wait(timeout_seconds=1))
        response = await _send_callback(
            port,
            f"/oauth/callback?code={_AUTHORIZATION_CODE}&state={_STATE}",
        )
        callback = await wait
        duplicate = await _send_callback(
            port,
            "/oauth/callback?code=another-code&state=another-state",
        )

    assert callback.code == _AUTHORIZATION_CODE
    assert callback.state == _STATE
    assert "200 OK" in response
    assert "Cache-Control: no-store" in response
    assert _AUTHORIZATION_CODE not in response
    assert _STATE not in response
    assert "409 Conflict" in duplicate
    assert "another-code" not in duplicate


async def test_loopback_callback_reports_denial_and_timeout_safely() -> None:
    port = _available_loopback_port()
    listener = cli._LoopbackCallbackServer(
        redirect_uri=AnyHttpUrl(f"http://127.0.0.1:{port}/oauth/callback"),
        bind_host=None,
    )

    async with listener:
        wait = asyncio.create_task(listener.wait(timeout_seconds=1))
        response = await _send_callback(
            port,
            "/oauth/callback?error=access_denied&error_description=sensitive",
        )
        with pytest.raises(cli.AuthCLIError, match="not approved"):
            await wait
    assert "400 Bad Request" in response
    assert "access_denied" not in response
    assert "sensitive" not in response

    second_port = _available_loopback_port()
    timeout_listener = cli._LoopbackCallbackServer(
        redirect_uri=AnyHttpUrl(f"http://127.0.0.1:{second_port}/oauth/callback"),
        bind_host=None,
    )
    async with timeout_listener:
        with pytest.raises(cli.AuthCLIError, match="Timed out"):
            await timeout_listener.wait(timeout_seconds=0.001)


async def test_login_persists_encrypted_credentials_without_printing_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    credential_dir = tmp_path / "credentials"
    key_file = tmp_path / "keys" / "monzo.key"
    secret_file = tmp_path / "client-secret"
    secret_file.write_text(_CLIENT_SECRET)
    os.chmod(secret_file, 0o600)
    port = _available_loopback_port()
    redirect_uri = f"http://127.0.0.1:{port}/oauth/callback"
    token = OAuthToken(
        access_token=SecretStr(_ACCESS_TOKEN),
        refresh_token=SecretStr(_REFRESH_TOKEN),
        expires_at=datetime.now(UTC) + timedelta(hours=6),
    )
    exchanges: list[tuple[str, str, str]] = []

    class FakeMonzoClient:
        def __init__(
            self,
            *,
            oauth: OAuthClientConfig,
            token_store: TokenStore,
        ) -> None:
            self.oauth = oauth
            self.token_store = token_store

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

        def create_authorization_request(self) -> AuthorizationRequest:
            return AuthorizationRequest(
                url=AnyHttpUrl(
                    f"https://auth.monzo.test/?client_id=client-safe-id&state={_STATE}"
                ),
                state=_STATE,
            )

        async def exchange_authorization_code(
            self,
            code: str,
            *,
            expected_state: str,
            returned_state: str,
        ) -> OAuthToken:
            validate_oauth_state(
                expected=expected_state,
                returned=returned_state,
            )
            exchanges.append((code, expected_state, returned_state))
            await self.token_store.save(token)
            return token

    monkeypatch.setattr(cli, "MonzoClient", FakeMonzoClient)
    namespace = cli._build_parser().parse_args(
        [
            "auth",
            "login",
            "--credential-dir",
            str(credential_dir),
            "--key-file",
            str(key_file),
            "--client-id",
            "client-safe-id",
            "--redirect-uri",
            redirect_uri,
            "--client-secret-file",
            str(secret_file),
            "--timeout-seconds",
            "2",
        ]
    )
    settings = CredentialSettings(
        credential_dir=credential_dir,
        key_file=key_file,
    )

    login = asyncio.create_task(cli._auth_login(namespace, settings))
    callback_response = await _send_when_ready(
        port,
        f"/oauth/callback?code={_AUTHORIZATION_CODE}&state={_STATE}",
    )
    await login

    output = capsys.readouterr().out
    encrypted = (credential_dir / "credentials.enc").read_bytes()
    assert exchanges == [(_AUTHORIZATION_CODE, _STATE, _STATE)]
    assert "authorization saved" in output
    assert _CLIENT_SECRET not in output
    assert _AUTHORIZATION_CODE not in output
    assert _ACCESS_TOKEN not in output
    assert _REFRESH_TOKEN not in output
    assert _AUTHORIZATION_CODE.encode() not in encrypted
    assert _ACCESS_TOKEN.encode() not in encrypted
    assert "200 OK" in callback_response
    assert _AUTHORIZATION_CODE not in callback_response
    assert _STATE not in callback_response

    async with ClientCredentialStore(
        credential_dir=credential_dir,
        key_file=key_file,
    ) as store:
        loaded_oauth = await store.load_oauth()
        loaded_token = await store.load()
    assert loaded_oauth is not None
    assert loaded_oauth.client_secret.get_secret_value() == _CLIENT_SECRET
    assert loaded_token == token


async def test_login_state_mismatch_never_persists_a_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_dir = tmp_path / "credentials"
    key_file = tmp_path / "keys" / "monzo.key"
    secret_file = tmp_path / "client-secret"
    secret_file.write_text(_CLIENT_SECRET)
    os.chmod(secret_file, 0o600)
    port = _available_loopback_port()

    class StateCheckingClient:
        def __init__(
            self,
            *,
            oauth: OAuthClientConfig,
            token_store: TokenStore,
        ) -> None:
            del oauth
            self.token_store = token_store

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

        def create_authorization_request(self) -> AuthorizationRequest:
            return AuthorizationRequest(
                url=AnyHttpUrl(f"https://auth.monzo.test/?state={_STATE}"),
                state=_STATE,
            )

        async def exchange_authorization_code(
            self,
            code: str,
            *,
            expected_state: str,
            returned_state: str,
        ) -> OAuthToken:
            del code
            validate_oauth_state(
                expected=expected_state,
                returned=returned_state,
            )
            raise AssertionError("state mismatch was accepted")

    monkeypatch.setattr(cli, "MonzoClient", StateCheckingClient)
    namespace = cli._build_parser().parse_args(
        [
            "auth",
            "login",
            "--credential-dir",
            str(credential_dir),
            "--key-file",
            str(key_file),
            "--client-id",
            "client-safe-id",
            "--redirect-uri",
            f"http://127.0.0.1:{port}/oauth/callback",
            "--client-secret-file",
            str(secret_file),
            "--timeout-seconds",
            "2",
        ]
    )
    settings = CredentialSettings(
        credential_dir=credential_dir,
        key_file=key_file,
    )

    login = asyncio.create_task(cli._auth_login(namespace, settings))
    await _send_when_ready(
        port,
        f"/oauth/callback?code={_AUTHORIZATION_CODE}&state=wrong-state",
    )
    with pytest.raises(MonzoConfigurationError, match="state validation"):
        await login

    async with ClientCredentialStore(
        credential_dir=credential_dir,
        key_file=key_file,
    ) as store:
        assert await store.load() is None


async def test_status_and_logout_are_secret_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    credential_dir = tmp_path / "credentials"
    key_file = tmp_path / "key"
    oauth = OAuthClientConfig(
        client_id="client-safe-id",
        client_secret=SecretStr(_CLIENT_SECRET),
        redirect_uri=AnyHttpUrl("http://127.0.0.1:8765/oauth/callback"),
    )
    token = OAuthToken(
        access_token=SecretStr(_ACCESS_TOKEN),
        refresh_token=SecretStr(_REFRESH_TOKEN),
        expires_at=datetime.now(UTC) + timedelta(hours=6),
    )
    async with ClientCredentialStore(
        credential_dir=credential_dir,
        key_file=key_file,
        create_key=True,
    ) as store:
        await store.save_oauth(oauth)
        await store.save(token)
    settings = CredentialSettings(
        credential_dir=credential_dir,
        key_file=key_file,
    )

    class LogoutClient:
        def __init__(
            self,
            *,
            oauth: OAuthClientConfig | None,
            token_store: TokenStore,
        ) -> None:
            del oauth
            self.token_store = token_store

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

        async def logout(self) -> None:
            await self.token_store.clear()

    monkeypatch.setattr(cli, "MonzoClient", LogoutClient)
    await cli._auth_status(settings)
    await cli._auth_logout(settings)

    output = capsys.readouterr().out
    assert "OAuth configured: yes" in output
    assert "Token configured: yes" in output
    assert "token invalidated and cleared" in output
    assert _CLIENT_SECRET not in output
    assert _ACCESS_TOKEN not in output
    assert _REFRESH_TOKEN not in output
    async with ClientCredentialStore(
        credential_dir=credential_dir,
        key_file=key_file,
    ) as store:
        assert await store.load() is None
        assert await store.load_oauth() == oauth
