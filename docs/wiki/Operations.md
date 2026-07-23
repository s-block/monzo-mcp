# Operations

This page covers normal service operation, credential maintenance, backup,
rotation, upgrades, scaling, and incident recovery.

## Endpoints

| Path | Authentication | Purpose |
| --- | --- | --- |
| `/mcp` | Endpoint bearer required | Native Streamable HTTP MCP endpoint |
| `/healthz` | None | Process readiness only |

`GET /healthz` returning `ok` means the process is accepting traffic. It does
not prove Monzo authorization, broker availability, or tool success.

Use `monzo_connection_status` for a safe end-to-end credential test.

## Start, inspect, and stop

With Compose:

```bash
docker compose up -d
docker compose ps
docker compose logs --tail=100 monzo-mcp
docker compose stop monzo-mcp
```

Restart after configuration or secret-file changes:

```bash
docker compose up -d --force-recreate monzo-mcp
```

The server intentionally disables HTTP access logs. Normal output is sparse.
Never add debug logging that prints request headers, broker bodies, Monzo
responses, or tool results.

## Local credential status

Stop the local-mode server first. It holds the credential-directory lock for
its lifetime.

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

The output contains only:

- whether an OAuth client is configured;
- whether a token exists;
- the token expiry, if known; and
- whether refresh is available.

## Reauthorization and logout

Reauthorize when:

- no token is configured;
- the refresh token is missing or invalid;
- Monzo rejects the authorization;
- the encryption key is lost;
- a stale backup was restored; or
- the OAuth client changed.

Procedure:

1. stop the server;
2. run `auth login` with the same credential and key mounts;
3. approve the connection in the Monzo mobile app;
4. start the server; and
5. call `monzo_connection_status`.

For logout, stop the server and run `auth logout` with the credential and key
mounts. The command attempts Monzo invalidation and always clears the local
token on authentication or transport failure. If remote invalidation cannot be
confirmed, also revoke the connection through Monzo.

## Backup and restore

For local mode:

1. stop the server;
2. verify no auth command is running;
3. copy the credential directory;
4. copy the encryption key to a separately protected location;
5. preserve owner-only permissions; and
6. restart the server.

Both components are sensitive. The encrypted bundle without the key is not
usable, but losing either component requires reauthorization.

Refresh-token rotation makes backups time-sensitive. A restored bundle may
contain a token Monzo has already invalidated. Do not overwrite the current
bundle with an older backup merely to fix an unrelated deployment problem.

## Rotation

### MCP endpoint bearer

1. generate a new independent random value in a new private file;
2. update all authorized MCP clients through their secret stores;
3. replace the mounted file;
4. recreate the container; and
5. verify old credentials fail and the new credential succeeds.

There is no overlap mechanism inside the server. Coordinate the cutover at
trusted ingress if zero downtime is required.

### OAuth client secret

Stop local mode and repeat `auth login` with the updated private secret file.
The new OAuth client configuration and token set are saved together.

### Encryption key

Do not overwrite the key for an existing encrypted bundle. Instead:

1. create a new key and empty credential directory;
2. complete a fresh Monzo login into that directory;
3. stop the server;
4. switch both mounts together; and
5. retain or securely dispose of the old pair according to policy.

### Broker signing or encryption keys

Rotate them in the trusted host and broker as one coordinated system. If a
verification overlap is needed, keep it shorter than the maximum delegation
lifetime and retire the old key deterministically.

## Upgrades and rollback

Before upgrading:

- read the changelog;
- back up local credentials;
- pin the new image digest;
- verify architecture compatibility; and
- test read-only discovery and one safe tool call.

After upgrading:

```bash
docker compose pull
docker compose up -d --remove-orphans
docker compose ps
curl --fail http://127.0.0.1:8000/healthz
```

Do not roll back local credential files. If the application image must be
rolled back, keep the newest valid credential bundle unless release notes
explicitly document a credential-format incompatibility.

## Scaling

Local mode is intentionally one process per credential bundle. Scale by
running independent containers with independent credentials, keys, endpoint
bearers, and network policy.

Broker mode can use multiple replicas because it stores no Monzo credential
state. The external broker must remain the single coordination point for each
credential's refresh and authorization.

Write-enabled MCP operation uses stateful Streamable HTTP for elicitation.
Ingress must preserve the relevant MCP session behavior. Read-only operation is
stateless.

## Failure and incident response

If a secret may be exposed:

1. stop or isolate the affected service;
2. identify which credential type was exposed;
3. rotate the endpoint bearer if MCP access was exposed;
4. revoke and reauthorize Monzo if an access token, refresh token, OAuth client
   secret, encryption key plus bundle, or running process was exposed;
5. rotate broker signing or encryption keys if applicable;
6. inspect narrowly scoped logs without copying financial data;
7. update clients and restart from a trusted image; and
8. document the root cause without recording secret values.

If transaction or balance data may be exposed, also review retention and access
in the MCP client, model provider, logs, traces, backups, and incident systems.
