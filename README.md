# monzo-mcp

[![CI](https://github.com/s-block/monzo-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/s-block/monzo-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/s-block/monzo-mcp/blob/main/LICENSE)
[![Python 3.12–3.14](https://img.shields.io/badge/python-3.12%E2%80%933.14-blue.svg)](https://www.python.org/)

`monzo-mcp` is an authenticated
[Model Context Protocol](https://modelcontextprotocol.io/) server for the
[Monzo Developer API](https://docs.monzo.com/). It uses the separately
distributed [`aiomonzo`](https://github.com/s-block/aiomonzo) package for its
fully asynchronous, typed Monzo integration.

It exposes data-minimized Monzo account, balance, pot, and transaction tools over
native Streamable HTTP. Financial writes are disabled by default. Monzo access
tokens, refresh tokens, OAuth client secrets, authorization codes, and raw bank
details are never exposed as MCP tool arguments or results.

The same Docker image supports two credential ownership models:

| Mode | Intended use | OAuth owner | Server state |
| --- | --- | --- | --- |
| `local` (default) | One person or one Monzo connection per container | `monzo-mcp` | Encrypted credential bundle on a mounted volume |
| `broker` | Trusted multi-user hosts and gateways | External credential broker | Stateless; no Monzo token persistence |

> [!WARNING]
> This project is alpha software that can access sensitive financial data.
> Start with read-only tools, use a dedicated private deployment, and review the
> security model before connecting it to an MCP client.

<!-- Separate the GitHub admonitions for standards-compliant Markdown rendering. -->

> [!IMPORTANT]
> Monzo states that its Developer API is not suitable for general public
> applications. This project is intended for personal, self-hosted, or explicitly
> permitted small-user integrations.

This project is independent and is not affiliated with or endorsed by Monzo.
Monzo is a trademark of its respective owner.

## Contents

- [Features](#features)
- [Available tools](#available-tools)
- [Requirements](#requirements)
- [Documentation](#documentation)
- [Installation](#installation)
- [Method 1: standalone local mode](#method-1-standalone-local-mode)
- [Method 2: external broker mode](#method-2-external-broker-mode)
- [MCP client configuration](#mcp-client-configuration)
- [Configuration reference](#configuration-reference)
- [Security model](#security-model)
- [Operations](#operations)
- [Troubleshooting](#troubleshooting)
- [Upstream Python client](#upstream-python-client)
- [Limitations](#limitations)
- [Development](#development)
- [Contributing and security reports](#contributing-and-security-reports)
- [License](#license)

## Features

- Native MCP Streamable HTTP transport at `/mcp`.
- Separate bearer authentication for the MCP endpoint.
- Exact `Host` validation and deny-by-default browser `Origin` handling.
- Fully asynchronous, typed Monzo API client built on `httpx`.
- Read-only MCP tools enabled by default.
- Data-minimized tool schemas and results.
- Bounded transaction pagination with a safe 30-day, 30-item default.
- Optional pot transfers and transaction annotations.
- Fail-closed MCP human confirmation before pot movements.
- Encrypted single-tenant OAuth storage with atomic refresh-token rotation.
- Stateless multi-user mode through a generic access-token broker.
- Non-root Docker image compatible with a read-only root filesystem.
- Python 3.12, 3.13, and 3.14 support.

## Available tools

### Read tools

These tools are always available:

| Tool | Purpose |
| --- | --- |
| `monzo_connection_status` | Verify that the configured Monzo authorization works |
| `monzo_list_accounts` | List data-minimized account summaries |
| `monzo_get_balance` | Read the balance for an explicit account |
| `monzo_list_pots` | List pots and balances for an explicit account |
| `monzo_get_transaction` | Read one data-minimized transaction |
| `monzo_list_transactions` | Read one bounded page of transactions |

Account numbers, sort codes, account owners, counterparty bank details, precise
merchant locations, raw metadata, and unknown provider extras are excluded from
MCP results.

Transaction lists default to the 30 days ending at the requested upper bound or
the current UTC time, with a limit of 30 entries. Callers can provide `since`,
`before`, and a limit up to 100. A full page returns `next_since` for
continuation. Money is represented as integer minor units, so `100` means
`£1.00` for GBP.

### Write tools

These tools are absent unless `MONZO_MCP_ENABLE_WRITES=true` or
`--enable-writes` is supplied:

| Tool | Purpose |
| --- | --- |
| `monzo_deposit_into_pot` | Move money from an account into a pot |
| `monzo_withdraw_from_pot` | Move money from a pot into an account |
| `monzo_annotate_transaction` | Set or remove transaction metadata |

Pot movements require:

- an explicit account and pot;
- a positive amount in integer minor units;
- a caller-supplied stable deduplication ID; and
- immediate affirmative MCP form elicitation.

If the MCP client does not support form elicitation, pot movements fail without
calling Monzo. OAuth, token management, logout, and webhook management are
human-only operations and are not exposed as model-facing tools.

## Requirements

For the Docker server:

- Docker Engine or Docker Desktop;
- an MCP client or gateway that supports Streamable HTTP and custom headers;
- a strong, separate MCP endpoint bearer stored in an owner-only file; and
- network access to `api.monzo.com`.

Local mode additionally requires:

- a confidential OAuth client from the
  [Monzo developer portal](https://developers.monzo.com/);
- a loopback redirect URI registered with Monzo;
- OpenSSL for generating the local encryption key and endpoint bearer; and
- a persistent writable credential directory.

Broker mode additionally requires:

- a trusted credential broker that owns Monzo OAuth storage and refresh;
- a short-lived delegation generated for every Monzo tool call; and
- private connectivity from the MCP container to the broker.

Python development requires Python 3.12 or newer and
[uv](https://docs.astral.sh/uv/).

## Documentation

The [documentation hub](https://github.com/s-block/monzo-mcp/blob/main/docs/README.md)
provides task-oriented guides in
addition to this README:

| Guide | Covers |
| --- | --- |
| [Getting started](https://github.com/s-block/monzo-mcp/wiki/Getting-Started) | Choosing a mode, building the image, and connecting an MCP client |
| [Standalone local mode](https://github.com/s-block/monzo-mcp/wiki/Standalone-Local-Mode) | Single-tenant OAuth, encrypted storage, login, and logout |
| [External broker mode](https://github.com/s-block/monzo-mcp/wiki/External-Broker-Mode) | Stateless multi-user deployment and the broker contract |
| [Configuration](https://github.com/s-block/monzo-mcp/wiki/Configuration) | Environment variables, CLI flags, validation, and precedence |
| [Tools and data](https://github.com/s-block/monzo-mcp/wiki/Tools-and-Data) | Tool schemas, pagination, money values, and data minimization |
| [Security](https://github.com/s-block/monzo-mcp/wiki/Security) | Trust boundaries, hardening, credential handling, and limitations |
| [Operations](https://github.com/s-block/monzo-mcp/wiki/Operations) | Health, backup, rotation, upgrades, scaling, and recovery |
| [Troubleshooting](https://github.com/s-block/monzo-mcp/wiki/Troubleshooting) | Common startup, OAuth, HTTP, broker, and Monzo failures |
| [Python client](https://github.com/s-block/monzo-mcp/wiki/Python-Client) | Using the separate `aiomonzo` package independently of MCP |
| [Development](https://github.com/s-block/monzo-mcp/wiki/Development) | Repository layout, checks, contribution workflow, and releases |

The same pages are published in the repository's
[GitHub Wiki](https://github.com/s-block/monzo-mcp/wiki).

## Installation

### Build from source

```bash
git clone https://github.com/s-block/monzo-mcp.git
cd monzo-mcp
docker build --tag monzo-mcp:local .
export MONZO_MCP_IMAGE=monzo-mcp:local
```

### Use the published image

Successful builds from `main` are published for Linux amd64 and arm64:

```bash
docker pull ghcr.io/s-block/monzo-mcp:latest
export MONZO_MCP_IMAGE=ghcr.io/s-block/monzo-mcp:latest
```

For repeatable deployments, replace `latest` with an immutable `sha-<commit>`
tag or an image digest.

## Method 1: standalone local mode

Local mode is the default. One container owns one Monzo OAuth authorization,
refreshes it when needed, and atomically saves Monzo's rotated token set.

### 1. Register the OAuth redirect URI

Create a confidential OAuth client in the
[Monzo developer portal](https://developers.monzo.com/) and register this exact
redirect URI:

```text
http://127.0.0.1:8765/oauth/callback
```

Keep the client ID available. The client secret will be supplied through a
private file, not an environment variable or command-line value.

### 2. Create private files

```bash
MONZO_MCP_CONFIG_DIR="$HOME/.config/monzo-mcp"
install -d -m 700 \
  "$MONZO_MCP_CONFIG_DIR" \
  "$MONZO_MCP_CONFIG_DIR/credentials"
umask 077
openssl rand -base64 32 > "$MONZO_MCP_CONFIG_DIR/monzo-mcp.key"
openssl rand -hex 32 > "$MONZO_MCP_CONFIG_DIR/monzo-mcp-endpoint-token"
printf 'Monzo client secret: '
IFS= read -r -s monzo_mcp_client_secret
printf '\n'
printf '%s\n' "$monzo_mcp_client_secret" \
  > "$MONZO_MCP_CONFIG_DIR/monzo-client-secret"
unset monzo_mcp_client_secret
chmod 600 \
  "$MONZO_MCP_CONFIG_DIR/monzo-mcp.key" \
  "$MONZO_MCP_CONFIG_DIR/monzo-mcp-endpoint-token" \
  "$MONZO_MCP_CONFIG_DIR/monzo-client-secret"
```

These files have different purposes:

| File | Purpose | Container access |
| --- | --- | --- |
| `monzo-mcp.key` | Encrypts and decrypts the local credential bundle | Read-only |
| `monzo-mcp-endpoint-token` | Authenticates MCP clients to this server | Read-only |
| `monzo-client-secret` | Used only during human OAuth login | Read-only, login only |
| `credentials/credentials.enc` | Encrypted OAuth client and token bundle | Read/write |

Never use a Monzo token as the MCP endpoint token.

### 3. Complete human OAuth login

Replace `YOUR_MONZO_CLIENT_ID` below. The command prints an authorization URL and
waits for the loopback callback:

```bash
MONZO_MCP_CONFIG_DIR="$HOME/.config/monzo-mcp"
docker run --rm -it \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --user "$(id -u):$(id -g)" \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m,mode=1777 \
  --publish 127.0.0.1:8765:8765 \
  --mount "type=bind,src=$MONZO_MCP_CONFIG_DIR/credentials,dst=/credentials" \
  --mount "type=bind,src=$MONZO_MCP_CONFIG_DIR/monzo-mcp.key,dst=/run/secrets/monzo-mcp.key,readonly" \
  --mount "type=bind,src=$MONZO_MCP_CONFIG_DIR/monzo-client-secret,dst=/run/secrets/monzo-client-secret,readonly" \
  "$MONZO_MCP_IMAGE" \
  auth login \
  --client-id YOUR_MONZO_CLIENT_ID \
  --redirect-uri http://127.0.0.1:8765/oauth/callback \
  --client-secret-file /run/secrets/monzo-client-secret \
  --callback-bind 0.0.0.0
```

Open the printed URL, complete authorization, and approve the new connection in
the Monzo mobile app. The encrypted credential bundle is written to the mounted
directory. Tokens and the OAuth client secret are never printed.

### 4. Start the server

```bash
MONZO_MCP_CONFIG_DIR="$HOME/.config/monzo-mcp"
docker run --rm --name monzo-mcp \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --user "$(id -u):$(id -g)" \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m,mode=1777 \
  --publish 127.0.0.1:8000:8000 \
  --mount "type=bind,src=$MONZO_MCP_CONFIG_DIR/credentials,dst=/credentials" \
  --mount "type=bind,src=$MONZO_MCP_CONFIG_DIR/monzo-mcp.key,dst=/run/secrets/monzo-mcp.key,readonly" \
  --mount "type=bind,src=$MONZO_MCP_CONFIG_DIR/monzo-mcp-endpoint-token,dst=/run/secrets/monzo-mcp-endpoint-token,readonly" \
  --env MONZO_MCP_HTTP_HOST=0.0.0.0 \
  --env MONZO_MCP_HTTP_ALLOWED_HOSTS=127.0.0.1:8000 \
  "$MONZO_MCP_IMAGE"
```

`MONZO_MCP_ACCESS_TOKEN_PROVIDER` is intentionally omitted because `local` is
the default. The root filesystem, key, and endpoint token are read-only. The
credential directory is the only persistent writable mount.

The endpoints are:

- MCP: `http://127.0.0.1:8000/mcp`
- Health: `http://127.0.0.1:8000/healthz`

The health endpoint is intentionally unauthenticated and returns only process
readiness.

## Method 2: external broker mode

Broker mode is for a trusted multi-user MCP host or gateway that already owns
user identity, Monzo OAuth storage, refresh, and refresh locking.

Set `MONZO_MCP_ACCESS_TOKEN_PROVIDER=broker` explicitly. In this mode the
container:

- never opens a local credential bundle;
- never receives or stores a Monzo refresh token;
- does not cache Monzo access tokens between requests; and
- exchanges one short-lived delegation for a usable access token when a Monzo
  tool runs.

### Broker-mode container

Create only the separate MCP endpoint bearer if one does not already exist:

```bash
MONZO_MCP_CONFIG_DIR="$HOME/.config/monzo-mcp"
install -d -m 700 "$MONZO_MCP_CONFIG_DIR"
umask 077
openssl rand -hex 32 > "$MONZO_MCP_CONFIG_DIR/monzo-mcp-endpoint-token"
chmod 600 "$MONZO_MCP_CONFIG_DIR/monzo-mcp-endpoint-token"
```

Place the MCP container and credential broker on a private Docker network:

```bash
docker network create private-mcp
```

Start the MCP server after the broker is reachable on that network:

```bash
MONZO_MCP_CONFIG_DIR="$HOME/.config/monzo-mcp"
docker run --rm --name monzo-mcp \
  --network private-mcp \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --user "$(id -u):$(id -g)" \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m,mode=1777 \
  --publish 127.0.0.1:8000:8000 \
  --mount "type=bind,src=$MONZO_MCP_CONFIG_DIR/monzo-mcp-endpoint-token,dst=/run/secrets/monzo-mcp-endpoint-token,readonly" \
  --env MONZO_MCP_ACCESS_TOKEN_PROVIDER=broker \
  --env MONZO_MCP_HTTP_HOST=0.0.0.0 \
  --env MONZO_MCP_HTTP_ALLOWED_HOSTS=127.0.0.1:8000 \
  --env MONZO_MCP_TOKEN_BROKER_URL=http://credential-broker:8000/access-token \
  "$MONZO_MCP_IMAGE"
```

Do not expose an HTTP broker across an untrusted network. Use HTTPS when the
broker connection leaves a private, controlled network.

### Incoming MCP request

The trusted MCP host sends two distinct credentials:

```http
Authorization: Bearer <mcp-endpoint-token>
X-MCP-Credential-Delegation: Bearer <short-lived-delegation>
```

The first authenticates the caller to the MCP server. The second authorizes a
request to the private credential broker. Neither is forwarded to Monzo.

### Credential broker contract

For a normal Monzo tool call, `monzo-mcp` requests a usable access token:

```http
POST /access-token
Authorization: Bearer <short-lived-delegation>
Accept: application/json
Content-Type: application/json

{}
```

The broker returns:

```http
HTTP/1.1 200 OK
Content-Type: application/json
Cache-Control: no-store

{
  "access_token": "<usable-monzo-access-token>",
  "token_type": "Bearer",
  "expires_at": "2026-07-24T18:00:00Z"
}
```

`expires_at` may be `null` or omitted. Unknown response fields are rejected.

If Monzo rejects the supplied access token, the MCP server permits one retry and
sends only the rejected token's SHA-256 fingerprint:

```json
{
  "rejected_access_token_sha256": "<lowercase-hex-sha256>"
}
```

The broker should compare that fingerprint with its current token before
refreshing. This prevents a stale concurrent request from rotating Monzo's
one-time refresh token again after another request has already replaced it.

The broker is responsible for:

- validating the delegation and its signature;
- binding it to the correct user, MCP server, audience, and short expiry;
- authorizing access to exactly that user's Monzo credential;
- serializing refreshes;
- atomically storing Monzo's replacement access and refresh tokens;
- returning `401` or `403` for invalid delegation;
- returning `409` when the user must complete Monzo authorization again; and
- preventing token, delegation, and user-data logging.

The MCP server maps other non-success broker responses to a safe unavailable
error. It never returns broker response details or tokens to the model.

## MCP client configuration

All MCP clients use the native Streamable HTTP endpoint:

```text
http://127.0.0.1:8000/mcp
```

Every client must send:

```http
Authorization: Bearer <contents-of-monzo-mcp-endpoint-token>
```

Local mode needs no other authorization header.

Broker mode additionally requires the trusted host to issue and send exactly one
delegation header for every Monzo tool call:

```http
X-MCP-Credential-Delegation: Bearer <short-lived-delegation>
```

The hostname and optional port used by the MCP client must be present exactly in
`MONZO_MCP_HTTP_ALLOWED_HOSTS`. For example, a container-network URL of
`http://monzo-mcp:8000/mcp` normally requires:

```text
MONZO_MCP_HTTP_ALLOWED_HOSTS=monzo-mcp,monzo-mcp:8000
```

If browser-based MCP clients send an `Origin` header, explicitly allow only the
exact trusted origins that need access.

## Configuration reference

Command-line flags override the corresponding environment settings where the
CLI offers a flag. Secrets should be supplied through private files, not
environment variables.

### Common settings

| Environment variable | Default | Description |
| --- | --- | --- |
| `MONZO_MCP_ACCESS_TOKEN_PROVIDER` | `local` | Access-token provider: `local` or `broker` |
| `MONZO_MCP_ENABLE_WRITES` | `false` | Expose mutation tools |
| `MONZO_MCP_ENDPOINT_TOKEN_FILE` | `/run/secrets/monzo-mcp-endpoint-token` | Owner-only file containing the MCP endpoint bearer |
| `MONZO_MCP_HTTP_HOST` | `0.0.0.0` | HTTP bind host |
| `MONZO_MCP_HTTP_PORT` | `8000` | HTTP listen port |
| `MONZO_MCP_HTTP_ALLOWED_HOSTS` | Loopback defaults only | Comma-separated exact `Host` values; required for container binds |
| `MONZO_MCP_HTTP_ALLOWED_ORIGINS` | Empty | Comma-separated exact browser origins |
| `MONZO_MCP_HTTP_MAX_REQUEST_BODY_BYTES` | `1048576` | Maximum authenticated request body; allowed range is 1024–16777216 bytes |
| `MONZO_MCP_HTTP_MAX_CONCURRENT_REQUESTS` | `100` | Maximum concurrent Uvicorn connections/tasks; allowed range is 1–10000 |

The endpoint bearer must contain between 32 and 8192 bytes. Secret files must be
regular, non-symlink files inaccessible to group and other users.

### Local-mode settings

| Environment variable | Default | Description |
| --- | --- | --- |
| `MONZO_MCP_CREDENTIAL_DIR` | `/credentials` | Owner-only writable directory containing the encrypted bundle and lock |
| `MONZO_MCP_KEY_FILE` | `/run/secrets/monzo-mcp.key` | Owner-only encryption-key file |

Broker settings supplied while the provider remains `local` are rejected. This
prevents accidental implicit switching to multi-user behavior.

### Broker-mode settings

| Environment variable | Default | Description |
| --- | --- | --- |
| `MONZO_MCP_TOKEN_BROKER_URL` | Required | Private HTTP or HTTPS access-token endpoint |
| `MONZO_MCP_DELEGATION_HEADER_NAME` | `X-MCP-Credential-Delegation` | Incoming header containing the broker delegation |
| `MONZO_MCP_TOKEN_BROKER_TIMEOUT_SECONDS` | `5` | Broker timeout; greater than zero and at most 30 seconds |

Local credential paths are ignored in explicit broker mode.

See the
[commented environment template](https://github.com/s-block/monzo-mcp/blob/main/.env.example).
The Docker image entrypoint is `monzo-mcp`, and its default command is `serve`.
Run `monzo-mcp --help` or `monzo-mcp <command> --help` for CLI options.

## Security model

### Separate credentials and audiences

The MCP endpoint bearer and Monzo access token have different audiences and
lifecycles:

```text
MCP client or gateway
  | Authorization: Bearer <MCP endpoint token>
  v
monzo-mcp
  | Authorization: Bearer <Monzo access token>
  v
Monzo Developer API
```

The endpoint bearer is validated in constant time and stripped before MCP tool
context is created. The Monzo bearer is acquired only inside the typed Monzo
client and added only to the outbound Monzo API request.

Do not pass a Monzo access token through the MCP `Authorization` header. MCP
authorization and upstream API authorization must remain separate.

### Local-mode storage

Local mode stores one OAuth client configuration and token set in an AES-256-GCM
encrypted bundle. The implementation provides:

- a separate 256-bit encryption-key file;
- authenticated encryption with versioned associated data;
- owner-only file and directory validation;
- symlink rejection;
- bounded file reads;
- non-blocking filesystem work outside the async event loop;
- a non-blocking process-lifetime directory lock;
- fresh nonces for every write;
- atomic file replacement and directory synchronization;
- one shared process-wide refresh lock; and
- removal of decrypted store state during shutdown.

Monzo refresh tokens are one-time credentials. If Monzo accepts a refresh but
the network loses the response, the previous refresh token may no longer work.
Human reauthorization is the recovery path.

### Broker-mode statelessness

Broker mode keeps no local Monzo credential store or token cache. Horizontal
replicas are safe only when:

- every request carries a valid user- and server-bound delegation;
- the broker is the sole owner of refresh and persistence;
- refresh operations are serialized centrally; and
- the MCP endpoint bearer and broker route remain behind trusted ingress.

### HTTP deployment

The container serves plain HTTP. Keep it on loopback or a private network. For
remote access:

- terminate TLS at a trusted reverse proxy;
- restrict ingress to intended MCP clients;
- configure exact allowed hosts and origins;
- retain the application's request-body and concurrency limits;
- enforce per-client rate, connection, and idle-time limits at trusted ingress;
- rotate the endpoint bearer through a secret store;
- disable proxy-header trust unless deliberately configured outside this image;
- do not include credentials in URLs; and
- prevent sensitive tool results from entering unnecessary model context,
  logs, traces, or chat retention.

HTTP access logs remain disabled. Sanitized `security_event=...` warnings cover
endpoint authentication, transport policy, request-size, broker, Monzo
authentication, and rate-limit failures without recording credentials, bodies,
tool inputs, or financial results.

The static endpoint bearer is not a full MCP OAuth authorization-server
implementation.

## Operations

### Credential status

Stop the local-mode server before running an auth command because the server
holds the credential-directory lock for its lifetime:

```bash
MONZO_MCP_CONFIG_DIR="$HOME/.config/monzo-mcp"
docker run --rm \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$MONZO_MCP_CONFIG_DIR/credentials,dst=/credentials" \
  --mount "type=bind,src=$MONZO_MCP_CONFIG_DIR/monzo-mcp.key,dst=/run/secrets/monzo-mcp.key,readonly" \
  "$MONZO_MCP_IMAGE" auth status
```

The command prints only whether OAuth and a token are configured, the token
expiry, and whether refresh is available.

### Reauthorization

Stop the server, repeat `auth login`, approve the connection in the Monzo mobile
app, and restart the server. Use the same credential and key mounts.

### Logout

Stop the server and replace `auth status` in the status command with
`auth logout`. The command attempts to invalidate the token at Monzo and clears
the local token. The encrypted OAuth client configuration remains available for
the next login.

### Backup and restore

Stop the server before copying the credential bundle. Back up the encrypted
credential directory and encryption key separately. Treat both backups as
sensitive.

A stale backup may contain a refresh token that Monzo has already rotated and
invalidated. Restoring such a backup requires human reauthorization. Losing the
encryption key also requires reauthorization.

### Rotation

- Endpoint bearer: replace the endpoint-token file, restart the server, and
  update every MCP client.
- OAuth client secret: repeat local `auth login` with the new secret file.
- Encryption key: complete a fresh login into a new credential directory and
  switch mounts; do not overwrite the key for an existing encrypted bundle.
- Broker delegation signing key: rotate in the trusted host and broker together,
  allowing only a tightly bounded overlap if required.

### Health checks

`GET /healthz` returns `ok` when the process is accepting traffic. It does not
validate Monzo or broker connectivity and intentionally returns no credential or
user information.

## Troubleshooting

### `Invalid MCP HTTP server settings`

Check that:

- the endpoint-token path is absolute;
- the token file is a regular file with mode `0600`;
- the token is at least 32 bytes;
- `MONZO_MCP_HTTP_ALLOWED_HOSTS` contains the exact hostname and optional port
  used by the client; and
- every allowed origin is an exact `http` or `https` origin without credentials,
  a path, query, or fragment.

### `Local Monzo credentials are incomplete`

Stop the server and run `auth login` with the same credential and key mounts.
Then approve the connection in the Monzo mobile app.

### `Credential directory is already in use`

Another server or auth command owns the bundle. Stop it before starting a second
process. Do not run multiple local-mode replicas against one credential
directory.

### Monzo asks for verification

Approve the connection in the Monzo mobile app. More than five minutes after
authentication, Monzo limits transaction-history access to its documented recent
window. Use an explicit lookback of at most 89 days, or complete fresh Monzo
verification before requesting older history.

### Endpoint returns `401 Unauthorized`

Verify that the client sends exactly one header:

```http
Authorization: Bearer <mcp-endpoint-token>
```

Use the endpoint token, not the Monzo token or broker delegation.

### Broker authorization fails

Verify that broker mode is explicit, the broker URL is reachable, and the
trusted host sends exactly one correctly named delegation header. Ensure the
delegation is unexpired and bound to the intended user, server, and audience.

## Upstream Python client

The lower-level Monzo API integration is published separately as
[`aiomonzo`](https://pypi.org/project/aiomonzo/) and is installed from PyPI as
a runtime dependency. Applications that do not need MCP should depend on that
package directly:

```bash
uv add aiomonzo
```

For short-lived local development with a Monzo API Playground token:

```python
from aiomonzo import MonzoClient


async def read_balance(playground_token: str) -> int:
    async with MonzoClient(access_token=playground_token) as monzo:
        accounts = await monzo.list_accounts()
        balance = await monzo.get_balance(accounts[0].id)
        return balance.balance
```

The client supports:

- static access tokens;
- OAuth through a `TokenStore`; and
- an injected `AccessTokenProvider`.

`AccessTokenProvider.get_access_token()` supplies a usable token.
`refresh_after_rejection(rejected_access_token)` resolves the one permitted
authentication retry. Provider implementations own persistence and refresh;
the Monzo client owns neither.

All network operations are asynchronous, use bounded timeouts, preserve
cancellation, and return typed models or typed, secret-safe exceptions.
See the [`aiomonzo` documentation](https://github.com/s-block/aiomonzo/wiki)
for its complete API and OAuth guidance.

## Limitations

- The project is alpha and its public interfaces may change before 1.0.
- Monzo's Developer API is not intended for unrestricted public applications.
- Local mode supports one Monzo authorization and one process per credential
  bundle.
- Initial local authorization and reauthorization are operator-run CLI actions;
  MCP URL elicitation is not currently implemented.
- The MCP transport is Streamable HTTP only; stdio and SSE are not exposed.
- The endpoint uses a static bearer rather than full MCP OAuth discovery.
- The container does not terminate TLS.
- Broker mode requires a separately implemented trusted credential broker.
- Docker MCP Catalog compatibility is not claimed because no catalog manifest
  or gateway-specific smoke test is included.
- Monzo API permissions, Strong Customer Authentication, protected-pot rules,
  rate limits, and recent-transaction restrictions still apply.
- Financial results enter model context when tools are used and may be retained
  by the chosen MCP client or model provider.

## Development

Install locked development dependencies:

```bash
uv sync --dev --frozen
```

Run the complete Python gate:

```bash
make check
```

This checks Ruff formatting, Ruff linting including security rules, strict mypy,
public-PyPI-only lockfile sources, reviewed-baseline secret detection, all
tests, and source/wheel builds.

Run the container gate:

```bash
make docker-check
```

This builds the production image, verifies non-root execution, performs
authenticated local- and broker-mode MCP handshakes under hardened flags, scans
the Dockerfile, and scans the image for high and critical vulnerabilities.

Useful targets:

```bash
make help
make format
make docs-check
make type-check
make test
make test-cov
make docker-build
make docker-smoke
```

CI additionally runs dependency audits for Python 3.12–3.14, dependency review,
CodeQL, multi-platform image publication, SBOM generation, build provenance, and
GitHub artifact attestation.

### Project layout

```text
src/monzo_mcp/mcp/       MCP settings, provider modes, server, and tools
src/monzo_mcp/credentials.py
                         Encrypted local credential persistence
src/monzo_mcp/cli.py     Human OAuth and server entrypoint
tests/                   Mirrored unit, lifecycle, protocol, and Docker tests
```

## Contributing and security reports

Issues and pull requests are welcome. Before submitting a change:

1. keep the change focused and typed;
2. add tests for behavior and failure paths;
3. run `make check`;
4. run `make docker-check` for container, packaging, dependency, entrypoint, or
   security-related changes; and
5. confirm that no real credentials, account data, or private registry URLs are
   present in the diff.

Do not include Monzo tokens, OAuth secrets, endpoint bearers, transaction data,
or other personal financial information in issues, logs, test fixtures, or
screenshots. For a security vulnerability, follow the
[security policy](https://github.com/s-block/monzo-mcp/security/policy) and use
[private vulnerability reporting](https://github.com/s-block/monzo-mcp/security/advisories/new)
rather than opening a public issue with exploitable details.

## License

Licensed under the
[MIT License](https://github.com/s-block/monzo-mcp/blob/main/LICENSE).
