# Standalone local mode

Local mode is the default. One server process owns one Monzo authorization and
one encrypted credential bundle.

## What is stored

The writable credential directory contains:

```text
credentials/
├── .credentials.lock
└── credentials.enc
```

`credentials.enc` contains the OAuth client configuration and current access
and refresh tokens, encrypted with AES-256-GCM. The encryption key remains in a
separate read-only file. The server never places these values in MCP arguments,
results, logs, URLs, or environment variables.

## 1. Register a Monzo OAuth client

Create a **confidential** OAuth client in the
[Monzo developer portal](https://developers.monzo.com/) and register this exact
loopback redirect URI:

```text
http://127.0.0.1:8765/oauth/callback
```

A confidential client is required for refresh-token-based long-lived access.
Keep the client ID available and put the client secret in a private file.

## 2. Create private files

```bash
MONZO_MCP_CONFIG_DIR="$HOME/.config/monzo-mcp"
install -d -m 700 \
  "$MONZO_MCP_CONFIG_DIR" \
  "$MONZO_MCP_CONFIG_DIR/credentials"
umask 077
openssl rand -base64 32 > "$MONZO_MCP_CONFIG_DIR/monzo-mcp.key"
openssl rand -hex 32 \
  > "$MONZO_MCP_CONFIG_DIR/monzo-mcp-endpoint-token"
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

| Path | Purpose | Runtime access |
| --- | --- | --- |
| `monzo-mcp.key` | Encrypt and decrypt the local bundle | Read-only |
| `monzo-mcp-endpoint-token` | Authenticate MCP clients | Read-only |
| `monzo-client-secret` | Complete human OAuth login | Login command only |
| `credentials/credentials.enc` | Store the encrypted OAuth client and token set | Read/write |

The process user must own the directory and private files. Group or world
permissions, symlinks, and non-regular secret files are rejected.

## 3. Complete human OAuth login

Replace `YOUR_MONZO_CLIENT_ID`:

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

The command:

1. acquires the credential-directory lock;
2. starts a one-shot callback listener;
3. generates a random OAuth state value;
4. prints the Monzo authorization URL;
5. validates the returned state in constant time;
6. exchanges the authorization code; and
7. atomically saves the encrypted credential bundle.

Open the printed URL, complete authorization, and approve the connection in the
Monzo mobile app. The callback listener accepts only the configured loopback
path and stops when login completes or times out.

## 4. Start the MCP server

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

`MONZO_MCP_ACCESS_TOKEN_PROVIDER` is omitted because `local` is the default.
Only the credential directory is writable.

## Refresh behavior

The server refreshes shortly before expiry and retries once when Monzo rejects
the current access token. One process-wide async lock serializes refresh. A
successful refresh atomically replaces both tokens in the encrypted bundle
before another request may refresh.

Monzo refresh tokens are one-time credentials. Never run two local-mode
processes against one credential directory. The filesystem lock rejects this
configuration.

If Monzo accepts a refresh but its response is lost, the old refresh token may
already be invalid. Reauthorization is the recovery path.

## Status, reauthorization, and logout

Stop the server before any auth command because the server holds the directory
lock for its lifetime.

Status:

```bash
# Use the same hardened docker run command and mounts as login, then:
monzo-mcp auth status
```

Status prints only whether OAuth and a token exist, the expiry, and whether a
refresh token is available.

Reauthorization:

1. stop the server;
2. rerun `auth login` with the same mounts;
3. approve the connection in Monzo; and
4. restart the server.

Logout:

```bash
# Use the same credential and key mounts as status, then:
monzo-mcp auth logout
```

Logout attempts remote invalidation and clears the local token. The encrypted
OAuth client configuration remains for the next login.
