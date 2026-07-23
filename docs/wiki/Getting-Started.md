# Getting started

This guide takes you from a fresh checkout to an authenticated MCP endpoint.
Choose a credential mode first; the same image serves both.

## 1. Choose a credential mode

Use **standalone local mode** when one private container represents one Monzo
connection. The container completes Monzo OAuth, encrypts the OAuth client and
token set, refreshes access, and persists rotated refresh tokens.

Use **external broker mode** when a trusted host already owns multiple users,
their Monzo OAuth credentials, and refresh coordination. The MCP container
remains stateless and exchanges a request-scoped delegation for a usable Monzo
access token.

| Question | Local | Broker |
| --- | --- | --- |
| Is this one user's private deployment? | Yes | Optional |
| Must one container serve multiple users? | No | Yes |
| Does the MCP container persist Monzo credentials? | Encrypted | No |
| Does another service already own OAuth? | No | Yes |
| Can replicas share one local credential volume? | No | Not applicable |

If unsure, start with local mode. Broker mode is an integration contract, not a
shortcut around implementing secure OAuth ownership.

## 2. Get the image

Build the current checkout:

```bash
git clone https://github.com/s-block/monzo-mcp.git
cd monzo-mcp
docker build --tag monzo-mcp:local .
export MONZO_MCP_IMAGE=monzo-mcp:local
```

Or use a published image:

```bash
docker pull ghcr.io/s-block/monzo-mcp:latest
export MONZO_MCP_IMAGE=ghcr.io/s-block/monzo-mcp:latest
```

Use an immutable `sha-<commit>` tag or digest for repeatable deployments.

## 3. Create the MCP endpoint bearer

The endpoint bearer authenticates the MCP client to this server. It is separate
from every Monzo credential.

```bash
MONZO_MCP_CONFIG_DIR="$HOME/.config/monzo-mcp"
install -d -m 700 "$MONZO_MCP_CONFIG_DIR"
umask 077
openssl rand -hex 32 \
  > "$MONZO_MCP_CONFIG_DIR/monzo-mcp-endpoint-token"
chmod 600 "$MONZO_MCP_CONFIG_DIR/monzo-mcp-endpoint-token"
```

Never use a Monzo access token, refresh token, or OAuth client secret as the
endpoint bearer.

## 4. Configure the selected mode

- Continue with [Standalone Local Mode](Standalone-Local-Mode) to create a
  Monzo OAuth client, authorize it, and start the container.
- Continue with [External Broker Mode](External-Broker-Mode) if a trusted host
  and credential broker already own Monzo OAuth.

## 5. Connect the MCP client

The default endpoint is:

```text
http://127.0.0.1:8000/mcp
```

Every MCP request must include:

```http
Authorization: Bearer <contents-of-monzo-mcp-endpoint-token>
```

Local mode needs no additional credential header. Broker mode also requires a
fresh delegation on each request:

```http
X-MCP-Credential-Delegation: Bearer <short-lived-delegation>
```

The hostname and optional port used by the client must exactly match an entry
in `MONZO_MCP_HTTP_ALLOWED_HOSTS`.

## 6. Test the connection

First check process readiness:

```bash
curl --fail --silent http://127.0.0.1:8000/healthz
```

This returns `ok`; it does not test Monzo authorization.

Then connect with the MCP client, discover tools, and call
`monzo_connection_status`. A successful result is:

```json
{
  "authenticated": true
}
```

After initial local OAuth, approve the new connection in the Monzo mobile app
before testing account tools. If discovery works but tool calls fail, use
[Troubleshooting](Troubleshooting).

## Recommended first grants

Start with only:

- `monzo_connection_status`
- `monzo_list_accounts`
- `monzo_get_balance`
- `monzo_list_pots`
- `monzo_get_transaction`
- `monzo_list_transactions`

Keep writes disabled until the read path, data retention, and human-confirmation
behavior of the chosen MCP client have been reviewed.
