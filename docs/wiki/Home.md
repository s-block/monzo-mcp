# monzo-mcp

`monzo-mcp` is an authenticated
[Model Context Protocol](https://modelcontextprotocol.io/) server for the
[Monzo Developer API](https://docs.monzo.com/). It uses the separately
distributed [`aiomonzo`](https://github.com/s-block/aiomonzo) package for its
fully asynchronous, typed Monzo integration.

It runs as a Docker service over native Streamable HTTP and supports two
credential models:

| Mode | Best for | Monzo OAuth owner | Monzo token storage |
| --- | --- | --- | --- |
| `local` (default) | One person or one Monzo connection per container | `monzo-mcp` | AES-256-GCM encrypted bundle on a mounted volume |
| `broker` | Trusted multi-user hosts and gateways | External credential broker | None in `monzo-mcp` |

Read [Getting Started](Getting-Started) to choose a mode and connect a client.

## Documentation

- [Getting Started](Getting-Started)
- [Standalone Local Mode](Standalone-Local-Mode)
- [External Broker Mode](External-Broker-Mode)
- [Docker Deployment](Docker-Deployment)
- [Configuration](Configuration)
- [Tools and Data](Tools-and-Data)
- [Architecture](Architecture)
- [Security](Security)
- [Operations](Operations)
- [Troubleshooting](Troubleshooting)
- [aiomonzo Python Client](Python-Client)
- [Development](Development)
- [FAQ](FAQ)

## Important limits

- The project is alpha software that can access sensitive financial data.
- Monzo says its Developer API is not suitable for unrestricted public
  applications. Use it for personal, self-hosted, or explicitly permitted
  integrations.
- Read tools are enabled by default. Financial writes require an explicit
  opt-in, and pot transfers also require immediate human confirmation.
- The container serves plain HTTP. Keep it on loopback or a private network, or
  terminate TLS at trusted ingress.
- Financial tool results enter the selected MCP client's model context and may
  be retained by that client or its model provider.

This independent project is not affiliated with or endorsed by Monzo.
