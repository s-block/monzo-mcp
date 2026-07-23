# monzo-mcp documentation

This directory contains the source for the complete `monzo-mcp`
documentation. The pages under [`wiki/`](wiki/) are also published to the
[GitHub Wiki](https://github.com/s-block/monzo-mcp/wiki).

Start with the guide that matches what you are trying to do:

| Goal | Guide |
| --- | --- |
| Choose a deployment mode and connect an MCP client | [Getting started](wiki/Getting-Started.md) |
| Run one private Monzo connection per container | [Standalone local mode](wiki/Standalone-Local-Mode.md) |
| Run a stateless server behind a multi-user host | [External broker mode](wiki/External-Broker-Mode.md) |
| Deploy and harden the Docker service | [Docker deployment](wiki/Docker-Deployment.md) |
| Configure environment variables and CLI flags | [Configuration](wiki/Configuration.md) |
| Understand tools, inputs, outputs, and limits | [Tools and data](wiki/Tools-and-Data.md) |
| Understand request and credential flows | [Architecture](wiki/Architecture.md) |
| Review the security model | [Security](wiki/Security.md) |
| Operate, rotate, back up, and recover the service | [Operations](wiki/Operations.md) |
| Diagnose a failure | [Troubleshooting](wiki/Troubleshooting.md) |
| Use the async Python package without MCP | [Python client](wiki/Python-Client.md) |
| Develop or contribute | [Development](wiki/Development.md) |
| Read concise design answers | [FAQ](wiki/FAQ.md) |

## Documentation conventions

- Commands assume a POSIX shell, Docker, and a checkout named `monzo-mcp`.
- Replace values written as `YOUR_*` or `<...>` before running a command.
- Monetary values are integer minor units. For GBP, `100` is `£1.00`.
- The word *endpoint token* means the bearer that authenticates a client to
  `monzo-mcp`. It never means a Monzo access or refresh token.
- The word *delegation* means a short-lived broker credential carried on one
  MCP HTTP request. It never means a Monzo token.

## Keeping the Wiki in sync

The files under `docs/wiki/` are the canonical Wiki source. Review changes in
the repository first, then publish that directory to the separate
`monzo-mcp.wiki.git` repository. Do not edit secrets, account identifiers, or
real transaction data into examples.
