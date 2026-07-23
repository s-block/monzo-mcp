"""Build-independent authenticated HTTP smoke check for the Docker image."""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyHttpUrl, SecretStr

from monzo_mcp.client import OAuthClientConfig, OAuthToken
from monzo_mcp.credentials import ClientCredentialStore

_ENDPOINT_TOKEN = "docker-smoke-local-endpoint-token-" * 2
_MONZO_ACCESS_TOKEN = "docker-smoke-monzo-access-token"
_MONZO_REFRESH_TOKEN = "docker-smoke-monzo-refresh-token"
_MONZO_CLIENT_SECRET = "docker-smoke-monzo-client-secret"
_EXPECTED_TOOL = "monzo_connection_status"


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        address = candidate.getsockname()
    if not isinstance(address, tuple) or not isinstance(address[1], int):
        raise RuntimeError("Could not allocate a loopback port")
    return address[1]


async def _run_command(*arguments: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    return process.returncode or 0, output.decode(errors="replace")


async def _wait_until_ready(health_url: str) -> None:
    async with httpx.AsyncClient(timeout=1) as client:
        for _ in range(100):
            try:
                response = await client.get(health_url)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    raise RuntimeError("HTTP container did not become ready")


async def _verify_mcp(resource_url: str) -> None:
    async with (
        httpx.AsyncClient(
            headers={"Authorization": f"Bearer {_ENDPOINT_TOKEN}"},
            timeout=5,
        ) as http_client,
        streamable_http_client(
            resource_url,
            http_client=http_client,
        ) as (read_stream, write_stream, _session_id),
        ClientSession(read_stream, write_stream) as session,
    ):
        initialization = await session.initialize()
        tools = await session.list_tools()
    if initialization.serverInfo.name != "monzo-mcp":
        raise RuntimeError("Unexpected MCP server identity")
    if _EXPECTED_TOOL not in {tool.name for tool in tools.tools}:
        raise RuntimeError("Expected read tool is unavailable")


async def _write_local_credentials(root: Path) -> tuple[Path, Path]:
    credential_dir = root / "credentials"
    key_file = root / "monzo-mcp.key"
    async with ClientCredentialStore(
        credential_dir=credential_dir,
        key_file=key_file,
        create_key=True,
    ) as store:
        await store.save_oauth(
            OAuthClientConfig(
                client_id="docker-smoke-client",
                client_secret=SecretStr(_MONZO_CLIENT_SECRET),
                redirect_uri=AnyHttpUrl("http://127.0.0.1:8765/oauth/callback"),
            )
        )
        await store.save(
            OAuthToken(
                access_token=SecretStr(_MONZO_ACCESS_TOKEN),
                refresh_token=SecretStr(_MONZO_REFRESH_TOKEN),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                client_id="docker-smoke-client",
            )
        )
    return credential_dir, key_file


async def _smoke_container(
    image: str,
    *,
    mode: str,
    root: Path,
) -> None:
    port = _available_loopback_port()
    container_name = f"monzo-mcp-http-smoke-{mode}-{os.getpid()}"
    endpoint_token_file = root / f"{mode}-endpoint-token"
    endpoint_token_file.write_text(_ENDPOINT_TOKEN)
    os.chmod(endpoint_token_file, 0o600)
    arguments = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m,mode=1777",  # noqa: S108
        "--publish",
        f"127.0.0.1:{port}:8000",
        "--mount",
        (
            f"type=bind,source={endpoint_token_file},"
            "target=/run/secrets/monzo-mcp-endpoint-token,readonly"
        ),
        "--env",
        "MONZO_MCP_HTTP_HOST=0.0.0.0",
        "--env",
        f"MONZO_MCP_HTTP_ALLOWED_HOSTS=127.0.0.1:{port}",
    ]
    if mode == "local":
        credential_dir, key_file = await _write_local_credentials(root)
        arguments.extend(
            [
                "--mount",
                (f"type=bind,source={credential_dir},target=/credentials"),
                "--mount",
                (
                    f"type=bind,source={key_file},"
                    "target=/run/secrets/monzo-mcp.key,readonly"
                ),
            ]
        )
    elif mode == "broker":
        arguments.extend(
            [
                "--env",
                "MONZO_MCP_ACCESS_TOKEN_PROVIDER=broker",
                "--env",
                "MONZO_MCP_TOKEN_BROKER_URL=http://127.0.0.1:9/access-token",
            ]
        )
    else:
        raise RuntimeError("Unknown Docker smoke mode")
    arguments.append(image)
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        base_url = f"http://127.0.0.1:{port}"
        await _wait_until_ready(f"{base_url}/healthz")
        await _verify_mcp(f"{base_url}/mcp")
    finally:
        await _run_command("docker", "rm", "--force", container_name)
        output, _ = await process.communicate()
        decoded = output.decode(errors="replace")
        for secret in (
            _ENDPOINT_TOKEN,
            _MONZO_ACCESS_TOKEN,
            _MONZO_REFRESH_TOKEN,
            _MONZO_CLIENT_SECRET,
        ):
            if secret in decoded:
                raise RuntimeError("A credential was written to container logs")
        if process.returncode not in {0, 137, 143}:
            raise RuntimeError(f"HTTP container failed safely: {decoded}")


async def _smoke(image: str) -> None:
    with tempfile.TemporaryDirectory(prefix="monzo-mcp-http-smoke-") as temporary:
        root = Path(temporary)
        await _smoke_container(image, mode="local", root=root)
        await _smoke_container(image, mode="broker", root=root)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m tests.mcp.docker_http_smoke IMAGE")
    asyncio.run(_smoke(sys.argv[1]))


if __name__ == "__main__":
    main()
