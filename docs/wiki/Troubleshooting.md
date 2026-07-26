# Troubleshooting

Start with:

```bash
docker compose ps
docker compose logs --tail=100 monzo-mcp
curl --verbose http://127.0.0.1:8000/healthz
```

Avoid `--verbose` when sending authenticated MCP requests because it can print
the endpoint bearer.

## Startup failures

### `Invalid MCP HTTP server settings`

Check:

- `MONZO_MCP_ENDPOINT_TOKEN_FILE` is absolute;
- the endpoint-token file is regular, owner-only, and not a symlink;
- the endpoint bearer is at least 32 bytes;
- the process user can read the file;
- the HTTP port is from 1 through 65535;
- allowed hosts contain no wildcard, scheme, path, or whitespace; and
- allowed origins are exact `http` or `https` origins.

For a container bind, configure allowed hosts explicitly:

```text
MONZO_MCP_HTTP_ALLOWED_HOSTS=127.0.0.1:8000
```

### `MONZO_MCP_HTTP_ALLOWED_HOSTS is required for a container bind`

The server will not infer safe hostnames while listening on `0.0.0.0`.
Configure the exact hostname and optional port used by the client.

### `Local Monzo credentials are incomplete`

Stop the server and run `monzo-mcp auth login` with the same credential and key
mounts. Approve the connection in the Monzo mobile app, then restart.

### `Local Monzo credentials are invalid`

The stored token is not refreshable, has no expiry, uses an unexpected token
type, or does not match the stored OAuth client. Complete a fresh login.

### `Credential directory is already in use`

Another server or auth command holds the bundle lock. Stop it. Do not run
multiple local-mode replicas against one credential directory.

### Private-file errors

Check ownership and modes:

```bash
ls -ld "$HOME/.config/monzo-mcp"
ls -l "$HOME/.config/monzo-mcp"
chmod 700 "$HOME/.config/monzo-mcp" \
  "$HOME/.config/monzo-mcp/credentials"
chmod 600 "$HOME/.config/monzo-mcp/monzo-mcp.key" \
  "$HOME/.config/monzo-mcp/monzo-mcp-endpoint-token" \
  "$HOME/.config/monzo-mcp/monzo-client-secret"
```

If the container uses a fixed UID, that UID must own or be able to read the
mounts without broadening permissions to group or other users.

### Broker settings are rejected in local mode

Broker configuration never implicitly switches provider mode. Either remove
all broker variables or set:

```text
MONZO_MCP_ACCESS_TOKEN_PROVIDER=broker
```

## OAuth login failures

### Initial login requires client ID and redirect URI

Supply both:

```text
--client-id YOUR_MONZO_CLIENT_ID
--redirect-uri http://127.0.0.1:8765/oauth/callback
```

The client secret comes from `--client-secret-file` or the hidden prompt.

### Callback listener cannot bind

- Stop the process already using port 8765.
- Publish `127.0.0.1:8765:8765` for the login container.
- Use `--callback-bind 0.0.0.0` only inside that container.
- Ensure the registered redirect URI remains the exact loopback URL.

### OAuth callback state validation failed

Do not retry an old callback URL. Restart `auth login` and complete only the
newly printed authorization URL. This protects against callback substitution.

### Authorization succeeds but account tools fail

The returned token has no data permission until the connection is approved in
the Monzo mobile app. Open Monzo, approve the new connection, and retry.

### Login times out

The default wait is five minutes. Restart login and finish the browser and
in-app steps before the timeout, or set a positive `--timeout-seconds`.

## MCP connection failures

### `/healthz` works but `/mcp` returns `401`

Send exactly one:

```http
Authorization: Bearer <mcp-endpoint-token>
```

Use the contents of the endpoint-token file, not a Monzo token or broker
delegation. Check for accidental newline handling in the MCP client's secret
input.

### `/mcp` returns `403` or rejects Host/Origin

Add the exact client-visible host header to
`MONZO_MCP_HTTP_ALLOWED_HOSTS`. If a browser sends `Origin`, add only its exact
origin to `MONZO_MCP_HTTP_ALLOWED_ORIGINS`.

Do not solve this with `*`; wildcards are intentionally rejected.

### Discovery works but no write tools appear

Writes are disabled by default. Recreate the service with:

```text
MONZO_MCP_ENABLE_WRITES=true
```

Then rediscover tools. Review [Security](Security) first.

### Pot movement says confirmation is required

The MCP client must support structured form elicitation. If it does not, the
server fails closed and no Monzo transfer is attempted. Use a compatible client
or leave pot movements unavailable.

## Broker failures

### `The MCP credential delegation was rejected`

The request has no delegation, more than one header, a malformed Bearer value,
or the broker returned `401`/`403`.

Verify:

- the trusted host injects the header outside model/tool arguments;
- the header name matches exactly;
- the delegation is unexpired;
- its audience, user, integration, and scope are correct; and
- the broker trusts the signing or lookup key.

### `Monzo authorization is required`

The broker returned `409`. The user must complete the host's Monzo reconnect
flow. The MCP container has no OAuth UI in broker mode.

### `The credential broker is temporarily unavailable`

Check broker DNS, network membership, URL, TLS, response time, and exact JSON
schema. Redirects, non-`Bearer` token types, unknown fields, malformed JSON, and
timeouts fail closed.

### Authentication retry causes repeated refresh failures

The broker must compare the supplied rejected-token fingerprint under the same
per-credential lock used for refresh. If another request already stored a new
token, return it without refreshing again.

## Monzo API failures

### Monzo requires recent in-app verification

More than five minutes after authentication, transaction history is restricted
to the last 90 days. Use a `since` value within the last 89 days to avoid the
moving boundary, or complete fresh Monzo verification before asking for older
history.

### Rate limited

Honor the retry interval in the tool error when present. Reduce pagination,
avoid repeated broad history scans, and do not add unbounded automatic retries.

### Request timed out or service could not be reached

Check container DNS and egress to `https://api.monzo.com`. The client uses
bounded connection/read/write/pool timeouts and does not follow redirects.

### Monzo rejected authorization after it previously worked

Local mode retries once through its coordinated refresh. If that fails, stop
the service and reauthorize. In broker mode, verify the broker's refresh and
atomic replacement behavior, then reconnect the user if required.

## Transaction result questions

### Why are only 30 transactions returned?

The safe default is one page of 30 items over the last 30 days. Set an explicit
`limit` up to 100 and use `next_since` for another page.

### Why are fields missing?

MCP results intentionally omit raw metadata, bank details, precise merchant
locations, provider extras, and other fields that are not required for common
assistant tasks. Use the lower-level `aiomonzo` client in trusted application
code if a supported provider field is genuinely needed.
