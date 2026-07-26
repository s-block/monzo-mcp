# Docker deployment

The published artifact is a non-root Docker image whose entrypoint is
`monzo-mcp` and default command is `serve`.

## Image selection

For development:

```bash
docker build --tag monzo-mcp:local .
export MONZO_MCP_IMAGE=monzo-mcp:local
```

For a released image:

```bash
export MONZO_MCP_IMAGE=ghcr.io/s-block/monzo-mcp:latest
docker pull "$MONZO_MCP_IMAGE"
```

Prefer an immutable `sha-<commit>` tag or image digest outside local testing.
Published images support Linux amd64 and arm64.

## Hardened runtime baseline

The examples use:

- a read-only root filesystem;
- all Linux capabilities dropped;
- `no-new-privileges`;
- a non-root user;
- a small, non-executable temporary filesystem;
- read-only secret mounts;
- a single writable credential mount only in local mode; and
- a loopback-only published port.

Keep these controls when translating the command to Compose, Kubernetes, or
another container platform.

## Local-mode Compose service

Complete the OAuth login from [Standalone Local Mode](Standalone-Local-Mode)
before starting the long-running service.

Create `compose.yaml`:

```yaml
services:
  monzo-mcp:
    image: ${MONZO_MCP_IMAGE:-ghcr.io/s-block/monzo-mcp:latest}
    user: "${MONZO_MCP_UID:-10001}:${MONZO_MCP_GID:-10001}"
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    tmpfs:
      - /tmp:rw,noexec,nosuid,size=16m,mode=1777
    ports:
      - "127.0.0.1:8000:8000"
    environment:
      MONZO_MCP_HTTP_HOST: 0.0.0.0
      MONZO_MCP_HTTP_ALLOWED_HOSTS: 127.0.0.1:8000
    volumes:
      - ${MONZO_MCP_CONFIG_DIR}/credentials:/credentials
      - ${MONZO_MCP_CONFIG_DIR}/monzo-mcp.key:/run/secrets/monzo-mcp.key:ro
      - ${MONZO_MCP_CONFIG_DIR}/monzo-mcp-endpoint-token:/run/secrets/monzo-mcp-endpoint-token:ro
    restart: unless-stopped
```

Export the host paths and IDs before running Compose:

```bash
export MONZO_MCP_CONFIG_DIR="$HOME/.config/monzo-mcp"
export MONZO_MCP_UID="$(id -u)"
export MONZO_MCP_GID="$(id -g)"
export MONZO_MCP_IMAGE=ghcr.io/s-block/monzo-mcp:latest
docker compose up -d
```

The selected UID must own the credential directory and be able to read the
private key and endpoint-token files.

## Broker-mode Compose service

Create the private network once:

```bash
docker network create private-mcp
```

Use a service with no credential volume:

```yaml
services:
  monzo-mcp:
    image: ${MONZO_MCP_IMAGE:-ghcr.io/s-block/monzo-mcp:latest}
    user: "${MONZO_MCP_UID:-10001}:${MONZO_MCP_GID:-10001}"
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    tmpfs:
      - /tmp:rw,noexec,nosuid,size=16m,mode=1777
    ports:
      - "127.0.0.1:8000:8000"
    environment:
      MONZO_MCP_ACCESS_TOKEN_PROVIDER: broker
      MONZO_MCP_HTTP_HOST: 0.0.0.0
      MONZO_MCP_HTTP_ALLOWED_HOSTS: 127.0.0.1:8000
      MONZO_MCP_TOKEN_BROKER_URL: http://credential-broker:8000/access-token
    volumes:
      - ${MONZO_MCP_CONFIG_DIR}/monzo-mcp-endpoint-token:/run/secrets/monzo-mcp-endpoint-token:ro
    networks:
      - private-mcp
    restart: unless-stopped

networks:
  private-mcp:
    external: true
```

The separately deployed broker must join `private-mcp` and resolve as
`credential-broker`. Do not publish the broker port merely to make this
connection work.

## Reverse proxy and remote access

The container deliberately does not terminate TLS. For remote access:

1. publish the container only to trusted ingress;
2. terminate TLS at a maintained reverse proxy or gateway;
3. preserve the original `Authorization` header;
4. do not log request headers or MCP bodies;
5. retain the application's request-body and concurrency bounds;
6. enforce per-client rates, connection counts, body size, and idle duration;
7. use the external hostname in `MONZO_MCP_HTTP_ALLOWED_HOSTS`;
8. list only trusted browser origins in
   `MONZO_MCP_HTTP_ALLOWED_ORIGINS`; and
9. disable public access to `/mcp` unless an authenticated client genuinely
   needs it.

The application disables proxy-header trust. If ingress needs client address
information, make that an explicit, reviewed deployment concern instead of
trusting forwarded headers from arbitrary clients.

## Health checks

Container-level check:

```yaml
healthcheck:
  test:
    - CMD
    - wget
    - --quiet
    - --tries=1
    - --spider
    - http://127.0.0.1:8000/healthz
  interval: 30s
  timeout: 3s
  retries: 3
```

Verify the chosen image actually contains the command used by a platform health
check. The application contract itself is simply:

```http
GET /healthz

HTTP/1.1 200 OK

ok
```

This endpoint reports process readiness only. It intentionally does not contact
Monzo or the broker and does not reveal credential state.

## Updating

```bash
docker compose pull
docker compose up -d --remove-orphans
docker compose ps
docker compose logs --tail=100 monzo-mcp
```

Back up local-mode credentials before an upgrade. Never roll back to a stale
token bundle after Monzo has rotated its refresh token.
