"""Human credential commands and the HTTP MCP service entry point."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
import webbrowser
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Self
from urllib.parse import parse_qs, urlsplit

from pydantic import AnyHttpUrl, SecretStr, ValidationError

from monzo_mcp.client import (
    MonzoAuthenticationError,
    MonzoClient,
    MonzoClientError,
    OAuthClientConfig,
)
from monzo_mcp.credentials import (
    ClientCredentialStore,
    CredentialError,
    CredentialStatus,
)
from monzo_mcp.mcp.http import run_server
from monzo_mcp.mcp.settings import (
    CredentialSettings,
    HttpServerSettings,
    ServerConfigurationError,
    ServerSettings,
    access_token_provider_settings_from_environment,
)
from monzo_mcp.private_files import (
    PrivateTextFileError,
    read_private_text_file,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

_DEFAULT_CALLBACK_TIMEOUT_SECONDS = 300.0
_MAX_CALLBACK_HEADER_BYTES = 8192
_MAX_SECRET_BYTES = 8192
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_EXPLICIT_CONTAINER_BINDS = frozenset({"0.0.0.0", "::"})  # noqa: S104


class AuthCLIError(Exception):
    """A safe, actionable error for the human credential workflow."""


@dataclass(frozen=True, slots=True)
class _OAuthCallback:
    code: str
    state: str


class _LoopbackCallbackServer:
    """One-shot HTTP callback listener with no access logging."""

    def __init__(
        self,
        *,
        redirect_uri: AnyHttpUrl,
        bind_host: str | None,
    ) -> None:
        parsed = urlsplit(str(redirect_uri))
        if (
            parsed.scheme != "http"
            or parsed.hostname not in _LOOPBACK_HOSTS
            or parsed.port is None
            or parsed.query
            or parsed.fragment
        ):
            raise AuthCLIError(
                "Redirect URI must be a query-free loopback HTTP URL with a port"
            )
        selected_bind = bind_host or (
            "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
        )
        if selected_bind not in _LOOPBACK_HOSTS | _EXPLICIT_CONTAINER_BINDS:
            raise AuthCLIError("Callback listener must bind locally")
        self._bind_host = selected_bind
        self._port = parsed.port
        self._path = parsed.path or "/"
        self._server: asyncio.Server | None = None
        self._result: asyncio.Future[_OAuthCallback] | None = None

    async def __aenter__(self) -> Self:
        loop = asyncio.get_running_loop()
        self._result = loop.create_future()
        try:
            self._server = await asyncio.start_server(
                self._handle_connection,
                host=self._bind_host,
                port=self._port,
                limit=_MAX_CALLBACK_HEADER_BYTES,
            )
        except OSError:
            raise AuthCLIError(
                "OAuth callback listener could not bind the registered port"
            ) from None
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        result = self._result
        self._result = None
        if result is not None and not result.done():
            result.cancel()

    async def wait(self, *, timeout_seconds: float) -> _OAuthCallback:
        result = self._result
        if result is None:
            raise AuthCLIError("OAuth callback listener is not running")
        try:
            async with asyncio.timeout(timeout_seconds):
                return await asyncio.shield(result)
        except TimeoutError:
            raise AuthCLIError("Timed out waiting for Monzo authorization") from None

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        status = "400 Bad Request"
        body = "Authorization could not be completed. Return to the terminal."
        try:
            header = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"),
                timeout=5.0,
            )
            if len(header) > _MAX_CALLBACK_HEADER_BYTES:
                raise ValueError
            request_line = header.split(b"\r\n", maxsplit=1)[0].decode(
                "ascii",
                errors="strict",
            )
            method, target, protocol = request_line.split(" ", maxsplit=2)
            parsed = urlsplit(target)
            if (
                method != "GET"
                or protocol not in {"HTTP/1.0", "HTTP/1.1"}
                or parsed.path != self._path
            ):
                raise ValueError
            query = parse_qs(parsed.query, keep_blank_values=True)
            if "error" in query:
                result = self._result
                if result is None or result.done():
                    status = "409 Conflict"
                    body = "Authorization callback was already received."
                else:
                    result.set_exception(
                        AuthCLIError("Monzo authorization was not approved")
                    )
            else:
                code = _one_query_value(query, "code")
                state_value = _one_query_value(query, "state")
                result = self._result
                if result is None or result.done():
                    status = "409 Conflict"
                    body = "Authorization callback was already received."
                else:
                    result.set_result(_OAuthCallback(code=code, state=state_value))
                    status = "200 OK"
                    body = "Authorization received. Return to the terminal."
        except (
            UnicodeDecodeError,
            ValueError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            TimeoutError,
        ):
            pass
        finally:
            await _write_callback_response(writer, status=status, body=body)


def main(argv: Sequence[str] | None = None) -> None:
    """Run a human auth command or the HTTP MCP service."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        arguments = ["serve"]
    parser = _build_parser()
    namespace = parser.parse_args(arguments)
    try:
        if namespace.command == "serve":
            settings = ServerSettings.from_environment(
                enable_writes=namespace.enable_writes,
            )
            http_settings = HttpServerSettings.from_environment(
                host=namespace.host,
                port=namespace.port,
                endpoint_token_file=namespace.endpoint_token_file,
                allowed_hosts=(
                    tuple(namespace.allowed_hosts)
                    if namespace.allowed_hosts is not None
                    else None
                ),
                allowed_origins=(
                    tuple(namespace.allowed_origins)
                    if namespace.allowed_origins is not None
                    else None
                ),
            )
            provider_settings = access_token_provider_settings_from_environment(
                settings,
                credential_dir=namespace.credential_dir,
                key_file=namespace.key_file,
                broker_url=namespace.token_broker_url,
                delegation_header_name=namespace.delegation_header,
                broker_timeout_seconds=namespace.broker_timeout_seconds,
            )
            run_server(settings, http_settings, provider_settings)
            return
        asyncio.run(_run_auth(namespace))
    except (AuthCLIError, CredentialError, ServerConfigurationError) as error:
        parser.exit(2, f"Error: {error}\n")
    except MonzoClientError:
        parser.exit(2, "Error: Monzo authorization could not be completed\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monzo-mcp",
        description="Containerized Monzo MCP service and human OAuth helper.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser(
        "serve",
        help="Run the authenticated Streamable HTTP MCP service",
    )
    serve.add_argument(
        "--enable-writes",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Expose mutation tools; pot transfers still require human confirmation",
    )
    serve.add_argument("--host", help="HTTP bind host")
    serve.add_argument("--port", type=int, help="HTTP listen port")
    serve.add_argument(
        "--endpoint-token-file",
        type=Path,
        help="Absolute owner-only file containing the MCP endpoint bearer token",
    )
    _add_credential_paths(serve)
    serve.add_argument("--token-broker-url", help="Access-token broker endpoint")
    serve.add_argument(
        "--delegation-header",
        help="HTTP header carrying request-scoped broker delegation",
    )
    serve.add_argument(
        "--broker-timeout-seconds",
        type=float,
        help="Maximum access-token broker request duration",
    )
    serve.add_argument(
        "--allowed-host",
        action="append",
        dest="allowed_hosts",
        help="Exact allowed Host header; repeat to add values",
    )
    serve.add_argument(
        "--allowed-origin",
        action="append",
        dest="allowed_origins",
        help="Exact allowed browser Origin; repeat to add values",
    )

    auth = commands.add_parser("auth", help="Manage client-owned Monzo credentials")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)

    login = auth_commands.add_parser("login", help="Complete human browser login")
    _add_credential_paths(login)
    login.add_argument("--client-id", help="Monzo OAuth client ID")
    login.add_argument(
        "--redirect-uri",
        help="Exact loopback redirect URI registered with Monzo",
    )
    login.add_argument(
        "--client-secret-file",
        type=Path,
        help="Absolute owner-only file containing the Monzo client secret",
    )
    login.add_argument(
        "--callback-bind",
        help=(
            "Listener bind host; use 0.0.0.0 only inside Docker with a "
            "127.0.0.1 host port mapping"
        ),
    )
    login.add_argument(
        "--timeout-seconds",
        type=float,
        default=_DEFAULT_CALLBACK_TIMEOUT_SECONDS,
        help="Maximum time to wait for the browser callback",
    )
    login.add_argument(
        "--open-browser",
        action="store_true",
        help="Open the authorization URL after printing it",
    )

    status = auth_commands.add_parser(
        "status",
        help="Show non-secret credential state",
    )
    _add_credential_paths(status)

    logout = auth_commands.add_parser(
        "logout",
        help="Invalidate the current token and clear it locally",
    )
    _add_credential_paths(logout)
    return parser


def _add_credential_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--credential-dir",
        type=Path,
        help="Absolute MCP-host-owned credential directory",
    )
    parser.add_argument(
        "--key-file",
        type=Path,
        help="Absolute client-owned credential-encryption key file",
    )


async def _run_auth(namespace: argparse.Namespace) -> None:
    settings = CredentialSettings.from_environment(
        credential_dir=namespace.credential_dir,
        key_file=namespace.key_file,
    )
    if namespace.auth_command == "login":
        await _auth_login(namespace, settings)
    elif namespace.auth_command == "status":
        await _auth_status(settings)
    elif namespace.auth_command == "logout":
        await _auth_logout(settings)
    else:
        raise AuthCLIError("Unknown auth command")


async def _auth_login(
    namespace: argparse.Namespace,
    settings: CredentialSettings,
) -> None:
    if namespace.timeout_seconds <= 0:
        raise AuthCLIError("Callback timeout must be positive")
    store = ClientCredentialStore(
        credential_dir=settings.credential_dir,
        key_file=settings.key_file,
        create_key=True,
    )
    async with store:
        existing = await store.load_oauth()
        oauth = await _resolve_oauth(namespace, existing=existing)
        await store.save_oauth(oauth)

        async with MonzoClient(oauth=oauth, token_store=store) as monzo:
            authorization = monzo.create_authorization_request()
            listener = _LoopbackCallbackServer(
                redirect_uri=oauth.redirect_uri,
                bind_host=namespace.callback_bind,
            )
            async with listener:
                print("Open this Monzo authorization URL in your browser:")
                print(str(authorization.url))
                if namespace.open_browser:
                    await asyncio.to_thread(webbrowser.open, str(authorization.url))
                callback = await listener.wait(
                    timeout_seconds=namespace.timeout_seconds
                )
            await monzo.exchange_authorization_code(
                callback.code,
                expected_state=authorization.state,
                returned_state=callback.state,
            )
    print(
        "Monzo authorization saved. Approve the new connection in the Monzo app "
        "before using account tools."
    )


async def _auth_status(settings: CredentialSettings) -> None:
    async with ClientCredentialStore(
        credential_dir=settings.credential_dir,
        key_file=settings.key_file,
        allow_empty=True,
    ) as store:
        status = await store.status()
    print(_format_status(status))


async def _auth_logout(settings: CredentialSettings) -> None:
    store = ClientCredentialStore(
        credential_dir=settings.credential_dir,
        key_file=settings.key_file,
        allow_empty=True,
    )
    warning: str | None = None
    async with store:
        token = await store.load()
        if token is None:
            print("No Monzo token is configured.")
            return
        oauth = await store.load_oauth()
        try:
            async with MonzoClient(oauth=oauth, token_store=store) as monzo:
                await monzo.logout()
        except MonzoAuthenticationError:
            await store.clear()
        except MonzoClientError:
            await store.clear()
            warning = (
                "Local credentials were cleared, but remote Monzo logout "
                "could not be confirmed."
            )
    if warning is not None:
        raise AuthCLIError(warning)
    print("Monzo token invalidated and cleared.")


async def _resolve_oauth(
    namespace: argparse.Namespace,
    *,
    existing: OAuthClientConfig | None,
) -> OAuthClientConfig:
    supplied_configuration = (
        namespace.client_id is not None
        or namespace.redirect_uri is not None
        or namespace.client_secret_file is not None
    )
    if not supplied_configuration:
        if existing is None:
            raise AuthCLIError("Initial login requires --client-id and --redirect-uri")
        return existing
    if namespace.client_id is None or namespace.redirect_uri is None:
        raise AuthCLIError("--client-id and --redirect-uri must be supplied together")
    secret = await _read_or_prompt_secret(namespace.client_secret_file)
    try:
        return OAuthClientConfig(
            client_id=namespace.client_id,
            client_secret=SecretStr(secret),
            redirect_uri=namespace.redirect_uri,
        )
    except ValidationError:
        raise AuthCLIError("OAuth client configuration is invalid") from None


async def _read_or_prompt_secret(secret_file: Path | None) -> str:
    if secret_file is not None:
        try:
            return await asyncio.to_thread(
                read_private_text_file,
                secret_file,
                label="Client secret file",
                max_bytes=_MAX_SECRET_BYTES,
            )
        except PrivateTextFileError as error:
            raise AuthCLIError(str(error)) from None
    secret = await asyncio.to_thread(
        getpass.getpass,
        "Monzo client secret (input hidden): ",
    )
    if not secret:
        raise AuthCLIError("Monzo client secret must not be empty")
    return secret


def _one_query_value(query: dict[str, list[str]], name: str) -> str:
    values = query.get(name)
    if values is None or len(values) != 1 or not values[0]:
        raise ValueError
    return values[0]


async def _write_callback_response(
    writer: asyncio.StreamWriter,
    *,
    status: str,
    body: str,
) -> None:
    encoded = body.encode()
    response = (
        f"HTTP/1.1 {status}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(encoded)}\r\n"
        "Connection: close\r\n"
        "Cache-Control: no-store\r\n"
        "Referrer-Policy: no-referrer\r\n"
        "\r\n"
    ).encode() + encoded
    writer.write(response)
    with suppress(ConnectionError):
        await writer.drain()
    writer.close()
    with suppress(ConnectionError):
        await writer.wait_closed()


def _format_status(status: CredentialStatus) -> str:
    expires = status.expires_at.isoformat() if status.expires_at is not None else "n/a"
    return "\n".join(
        (
            f"OAuth configured: {'yes' if status.oauth_configured else 'no'}",
            f"Token configured: {'yes' if status.token_configured else 'no'}",
            f"Token expires: {expires}",
            f"Refresh available: {'yes' if status.refreshable else 'no'}",
        )
    )


if __name__ == "__main__":
    main()
