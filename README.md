# monzo-mcp

[![CI](https://github.com/s-block/monzo-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/s-block/monzo-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/s-block/monzo-mcp/blob/main/LICENSE)
[![Python 3.12–3.14](https://img.shields.io/badge/python-3.12%E2%80%933.14-blue.svg)](https://www.python.org/)

`monzo-mcp` lets MCP clients work with a Monzo account through the
[Monzo Developer API](https://docs.monzo.com/). It provides tools for reading
accounts, balances, pots, and transactions. Optional write tools can move money
between an account and a pot or add metadata to a transaction.

The server uses MCP's Streamable HTTP transport and is distributed as a Docker
image. Read tools are available by default; write tools have to be enabled when
the server starts.

> [!WARNING]
> This is alpha software with access to financial data. Run it as a private
> service and review your MCP client's data-handling settings before connecting
> an account.

Monzo states that its Developer API is not suitable for general public
applications. This project is intended for personal, self-hosted, or otherwise
permitted integrations.

This project is independent and is not affiliated with or endorsed by Monzo.

## Tools

The following read tools are always available:

| Tool | Description |
| --- | --- |
| `monzo_connection_status` | Check whether the current Monzo authorization works |
| `monzo_list_accounts` | List Monzo accounts |
| `monzo_get_balance` | Get the balance for an account |
| `monzo_list_pots` | List pots and their balances |
| `monzo_get_transaction` | Get a transaction |
| `monzo_list_transactions` | List a page of transactions |

Transaction lists return 30 items from the previous 30 days by default. The
date range and page size can be changed by the caller, up to 100 items per
request. Monetary values use integer minor units, so `100` represents `£1.00`
for GBP.

These write tools are added when `MONZO_MCP_ENABLE_WRITES=true` or
`--enable-writes` is used:

| Tool | Description |
| --- | --- |
| `monzo_deposit_into_pot` | Move money from an account into a pot |
| `monzo_withdraw_from_pot` | Move money from a pot into an account |
| `monzo_annotate_transaction` | Set or remove transaction metadata |

Pot transfers need a client that supports MCP form elicitation. The client asks
for confirmation immediately before the transfer is made.

Account lists and transaction results omit bank details and other fields that
are not needed by these tools. See
[Tools and data](docs/wiki/Tools-and-Data.md) for the complete inputs, outputs,
pagination rules, and Monzo API restrictions.

## Deployment modes

The same image supports two ways of supplying Monzo credentials:

| Mode | Use case | Credential storage |
| --- | --- | --- |
| `local` (default) | A private deployment for one Monzo connection | Encrypted files on a mounted volume |
| `broker` | A multi-user host that already manages Monzo OAuth | An external credential broker |

Most users should start with local mode. Broker mode is intended for an
existing service that can identify users, store their OAuth credentials, and
coordinate token refreshes.

## Getting started

You will need:

- Docker Engine or Docker Desktop;
- an MCP client that supports Streamable HTTP and custom headers;
- a confidential OAuth client from the
  [Monzo developer portal](https://developers.monzo.com/); and
- OpenSSL to generate the local secret files.

### 1. Get the image

Build the current source:

```bash
git clone https://github.com/s-block/monzo-mcp.git
cd monzo-mcp
docker build --tag monzo-mcp:local .
export MONZO_MCP_IMAGE=monzo-mcp:local
```

Alternatively, use the published image:

```bash
docker pull ghcr.io/s-block/monzo-mcp:latest
export MONZO_MCP_IMAGE=ghcr.io/s-block/monzo-mcp:latest
```

Use an immutable `sha-<commit>` tag or image digest for a repeatable
deployment.

### 2. Register the callback URL

Create a confidential OAuth client in the Monzo developer portal and register
this exact redirect URI:

```text
http://127.0.0.1:8765/oauth/callback
```

Keep the client ID to hand. The client secret will be saved in a private file
in the next step.

### 3. Create the local files

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

The credential directory stores the encrypted OAuth authorization. The
encryption key, MCP endpoint token, and OAuth client secret remain separate
files.

### 4. Authorize Monzo

Replace `YOUR_MONZO_CLIENT_ID` in this command:

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

Open the URL printed by the command and complete the login. Approve the new
connection in the Monzo mobile app when prompted.

### 5. Start the server

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

The MCP endpoint is `http://127.0.0.1:8000/mcp`. A health check is available at
`http://127.0.0.1:8000/healthz`.

### 6. Configure the MCP client

Point the client at:

```text
http://127.0.0.1:8000/mcp
```

Send the contents of `monzo-mcp-endpoint-token` as a bearer token:

```http
Authorization: Bearer <endpoint-token>
```

Once connected, call `monzo_connection_status`, followed by
`monzo_list_accounts`, to check the setup.

## Using a credential broker

Broker mode is available for multi-user MCP hosts that already manage Monzo
OAuth. Set:

```text
MONZO_MCP_ACCESS_TOKEN_PROVIDER=broker
MONZO_MCP_TOKEN_BROKER_URL=http://credential-broker:8000/access-token
```

Each incoming MCP request also needs a short-lived delegation header:

```http
X-MCP-Credential-Delegation: Bearer <delegation>
```

The broker endpoint, response schema, refresh behaviour, and deployment example
are documented in
[External broker mode](docs/wiki/External-Broker-Mode.md).

## Configuration

Command-line options override their corresponding environment variables. Run
`monzo-mcp --help` or `monzo-mcp <command> --help` to see the available
options.

### Server

| Variable | Default | Description |
| --- | --- | --- |
| `MONZO_MCP_ACCESS_TOKEN_PROVIDER` | `local` | Credential mode: `local` or `broker` |
| `MONZO_MCP_ENABLE_WRITES` | `false` | Add the write tools |
| `MONZO_MCP_ENDPOINT_TOKEN_FILE` | `/run/secrets/monzo-mcp-endpoint-token` | File containing the MCP bearer token |
| `MONZO_MCP_HTTP_HOST` | `0.0.0.0` | HTTP bind host |
| `MONZO_MCP_HTTP_PORT` | `8000` | HTTP listen port |
| `MONZO_MCP_HTTP_ALLOWED_HOSTS` | Loopback hosts | Comma-separated accepted `Host` values |
| `MONZO_MCP_HTTP_ALLOWED_ORIGINS` | Empty | Comma-separated accepted browser origins |
| `MONZO_MCP_HTTP_MAX_REQUEST_BODY_BYTES` | `1048576` | Maximum request body size |
| `MONZO_MCP_HTTP_MAX_CONCURRENT_REQUESTS` | `100` | Maximum concurrent requests |

`MONZO_MCP_HTTP_ALLOWED_HOSTS` must be set when the server binds to a
non-loopback address, including its default Docker bind.

### Local mode

| Variable | Default | Description |
| --- | --- | --- |
| `MONZO_MCP_CREDENTIAL_DIR` | `/credentials` | Writable encrypted credential directory |
| `MONZO_MCP_KEY_FILE` | `/run/secrets/monzo-mcp.key` | Credential encryption key |

Only one local-mode process can use a credential directory at a time.

### Broker mode

| Variable | Default | Description |
| --- | --- | --- |
| `MONZO_MCP_TOKEN_BROKER_URL` | Required | Credential broker endpoint |
| `MONZO_MCP_DELEGATION_HEADER_NAME` | `X-MCP-Credential-Delegation` | Incoming delegation header |
| `MONZO_MCP_TOKEN_BROKER_TIMEOUT_SECONDS` | `5` | Broker request timeout |

See
[`.env.example`](.env.example)
and the [configuration guide](docs/wiki/Configuration.md)
for validation rules and all CLI equivalents.

## Managing local credentials

The server holds the credential directory open while it runs. Stop the server
before using an auth command with the same directory.

To inspect the authorization:

```bash
monzo-mcp auth status
```

To replace an expired or invalid authorization, run `auth login` again and
restart the server.

To invalidate the current authorization and clear it locally:

```bash
monzo-mcp auth logout
```

When these commands run in Docker, use the same credential-directory and
encryption-key mounts as the login command. The
[operations guide](docs/wiki/Operations.md) covers backups, recovery, and
credential rotation.

## Security and deployment

Local credentials are encrypted at rest and the server requires a separate
bearer token for its MCP endpoint. Keep the service on loopback or a private
network. For remote access, put it behind a TLS-terminating reverse proxy and
restrict ingress to the intended MCP clients.

The Docker image runs as a non-root user and supports a read-only root
filesystem. The examples above also drop Linux capabilities and enable
`no-new-privileges`.

Read the [security guide](docs/wiki/Security.md) before enabling writes or
deploying the server remotely. Security issues should be reported through
[GitHub private vulnerability reporting](https://github.com/s-block/monzo-mcp/security/advisories/new).

## Limitations

- The project is alpha and its interfaces may change before version 1.0.
- Local mode supports one Monzo authorization per credential directory.
- OAuth login and reauthorization are command-line operations.
- Streamable HTTP is the only MCP transport.
- TLS termination is not included in the container.
- Broker mode requires a separate credential broker.
- Monzo permissions, verification requirements, protected-pot rules, rate
  limits, and transaction-history restrictions still apply.

## Documentation

The [documentation index](docs/README.md) links to versioned guides for local
setup, Docker deployment, broker integration, configuration, tools, operations,
and troubleshooting.

## Development

Install the locked development dependencies:

```bash
uv sync --dev --frozen
```

The complete checks also require Node.js for Markdown validation. Container
validation requires Docker.

Run the Python checks:

```bash
make check
```

Run the Docker build, smoke tests, and security scans:

```bash
make docker-check
```

Other useful targets include:

```bash
make help
make format
make docs-check
make type-check
make test
make test-cov
```

See the [development guide](docs/wiki/Development.md) and
[contribution guide](CONTRIBUTING.md) for the repository layout and
contribution workflow.

## Contributing

Issues and pull requests are welcome. Read
[`CONTRIBUTING.md`](CONTRIBUTING.md) and the
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) first. Keep credentials, account
details, and transaction data out of issues, test fixtures, logs, and
screenshots.

## License

Licensed under the
[MIT License](https://github.com/s-block/monzo-mcp/blob/main/LICENSE).
