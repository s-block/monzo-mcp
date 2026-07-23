# Configuration

Configuration is validated before the HTTP listener starts. Command-line flags
override corresponding environment values. Secret values are read from private
files rather than environment variables or command-line arguments.

## Common environment variables

| Variable | Default | Validation and purpose |
| --- | --- | --- |
| `MONZO_MCP_ACCESS_TOKEN_PROVIDER` | `local` | Exact value `local` or `broker` |
| `MONZO_MCP_ENABLE_WRITES` | `false` | Boolean: `true`/`false`, `1`/`0`, `yes`/`no`, or `on`/`off` |
| `MONZO_MCP_ENDPOINT_TOKEN_FILE` | `/run/secrets/monzo-mcp-endpoint-token` | Absolute private file containing a 32–8192-byte endpoint bearer |
| `MONZO_MCP_HTTP_HOST` | `0.0.0.0` | Bind host |
| `MONZO_MCP_HTTP_PORT` | `8000` | Integer from 1 through 65535 |
| `MONZO_MCP_HTTP_ALLOWED_HOSTS` | Loopback values only for a loopback bind | Comma-separated exact `Host` values |
| `MONZO_MCP_HTTP_ALLOWED_ORIGINS` | Empty | Comma-separated exact browser origins |

When binding to `0.0.0.0` or another non-loopback interface,
`MONZO_MCP_HTTP_ALLOWED_HOSTS` is required. Wildcards, schemes, paths, and
whitespace are rejected. Include both variants if clients may send both:

```text
MONZO_MCP_HTTP_ALLOWED_HOSTS=monzo-mcp,monzo-mcp:8000
```

An allowed origin must be an exact `http` or `https` origin without user info,
a path other than `/`, a query, or a fragment:

```text
MONZO_MCP_HTTP_ALLOWED_ORIGINS=https://mcp-client.example
```

Non-browser MCP clients normally omit `Origin`; an empty allowlist is therefore
the safe default.

## Local-mode environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `MONZO_MCP_CREDENTIAL_DIR` | `/credentials` | Absolute owner-only writable directory for `credentials.enc` and the lock |
| `MONZO_MCP_KEY_FILE` | `/run/secrets/monzo-mcp.key` | Absolute owner-only encryption-key file |

Broker-specific settings are rejected when the provider remains `local`. This
prevents accidental implicit activation of multi-user behavior.

## Broker-mode environment variables

| Variable | Default | Validation and purpose |
| --- | --- | --- |
| `MONZO_MCP_TOKEN_BROKER_URL` | Required | HTTP or HTTPS URL without user info, query, or fragment |
| `MONZO_MCP_DELEGATION_HEADER_NAME` | `X-MCP-Credential-Delegation` | Non-empty HTTP header name without whitespace or `:` |
| `MONZO_MCP_TOKEN_BROKER_TIMEOUT_SECONDS` | `5` | Greater than zero and no more than 30 |

Local credential paths are ignored in explicit broker mode.

## Private-file rules

The endpoint token, encryption key, and any OAuth client-secret file must:

- use an absolute path;
- be a regular file;
- not be a symlink;
- be owned and readable by the process user;
- have no group or other permission bits;
- fit within the implementation's bounded read size; and
- contain a non-empty value without surrounding whitespace ambiguity.

The endpoint bearer must be at least 32 bytes. Generate independent random
values for the endpoint bearer and encryption key.

## CLI

Running the image or executable with no arguments is equivalent to:

```bash
monzo-mcp serve
```

### `serve`

```text
monzo-mcp serve
  [--enable-writes | --no-enable-writes]
  [--host HOST]
  [--port PORT]
  [--endpoint-token-file PATH]
  [--credential-dir PATH]
  [--key-file PATH]
  [--token-broker-url URL]
  [--delegation-header NAME]
  [--broker-timeout-seconds SECONDS]
  [--allowed-host HOST]...
  [--allowed-origin ORIGIN]...
```

Repeat `--allowed-host` or `--allowed-origin` to provide multiple values.

### `auth login`

```text
monzo-mcp auth login
  [--credential-dir PATH]
  [--key-file PATH]
  [--client-id CLIENT_ID]
  [--redirect-uri URI]
  [--client-secret-file PATH]
  [--callback-bind HOST]
  [--timeout-seconds SECONDS]
  [--open-browser]
```

Initial login requires `--client-id` and `--redirect-uri`. The client secret is
read from `--client-secret-file` or a hidden terminal prompt. A later login can
reuse the stored OAuth client by omitting all three.

The redirect URI must be query-free loopback HTTP with an explicit port. The
callback bind defaults to the redirect host. `0.0.0.0` is accepted only to
support a container whose callback port is published on host loopback.

### `auth status`

```text
monzo-mcp auth status
  [--credential-dir PATH]
  [--key-file PATH]
```

Prints non-secret credential metadata only.

### `auth logout`

```text
monzo-mcp auth logout
  [--credential-dir PATH]
  [--key-file PATH]
```

Invalidates the current Monzo access token when possible, then clears the local
token. It retains the OAuth client configuration.

## Example file

The repository's
[`.env.example`](https://github.com/s-block/monzo-mcp/blob/main/.env.example)
documents every environment setting. It intentionally contains no secret
values. Do not put real endpoint bearers, encryption keys, Monzo tokens, or
OAuth client secrets in `.env` files.
