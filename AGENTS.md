# Monzo MCP Project Guide

Keep this local banking integration typed, async, minimal, and secret-safe.

## Architecture

- `src/monzo_mcp/client/` owns the fully async Monzo API client, OAuth, transport,
  typed provider models, and public client exceptions.
- `src/monzo_mcp/credentials.py` owns encrypted client-side credential storage.
- `src/monzo_mcp/mcp/` owns data-minimized MCP models, tools, server composition,
  and lifecycle.
- `src/monzo_mcp/cli.py` is the thin human auth and HTTP service entrypoint.
- Mirror these boundaries under `tests/`.

The MCP server is an authenticated Streamable HTTP service distributed as a
Docker image. It has two deliberate `AccessTokenProvider` modes:

- Unset or `local` is single-tenant. Durable credentials belong to the operator,
  are mounted into the container, and are atomically updated when Monzo rotates
  refresh tokens.
- Explicit `broker` is stateless and multi-user. The trusted MCP host owns
  OAuth, storage, refresh, and refresh locking; the container exchanges one
  request delegation for a usable access token.

## Security Invariants

- Never expose OAuth secrets, access tokens, refresh tokens, authorization codes,
  account numbers, sort codes, or raw Monzo metadata through MCP.
- Bearer tokens are loaded inside `MonzoClient` and added only to outbound Monzo
  API requests.
- Keep OAuth login, status, and logout human-only CLI operations; do not add them
  as MCP tools.
- Keep writes opt-in. Pot transfers require an explicit stable deduplication ID
  and fail-closed human elicitation.
- Preserve encrypted atomic credential replacement, owner-only modes, symlink
  rejection, and process-lifetime locking.
- In local mode, keep one OAuth provider and refresh lock for the complete
  process lifetime. Never run multiple replicas against one credential bundle.
- In broker mode, never open a local credential store, cache provider tokens, or
  persist request delegation.
- Do not log credentials or sensitive banking data.
- Keep Streamable HTTP as the only MCP transport. Require its separate strong
  endpoint bearer, exact Host validation, and complete settings for only the
  selected provider before listening.
- Default read-only operation to stateless JSON responses. Write-enabled
  operation is stateful because pot transfers require MCP elicitation.
- Keep the container non-root and compatible with a read-only root filesystem,
  dropped capabilities, and `no-new-privileges`.
- Do not add a database, Redis, webhook listener, or durable server state
  without an explicit architecture change.

## Python And Async Rules

- Support Python 3.12 through 3.14.
- Fully type new and changed functions; prefer Pydantic models at trust
  boundaries over loose dictionaries.
- Keep network paths non-blocking and preserve cancellation.
- Reuse the lifecycle-owned `httpx.AsyncClient`; do not create one per request.
- Move unavoidable filesystem work off the event loop.
- Use bounded timeouts and retries. Retry mutations only when idempotency makes
  replay safe.
- Preserve typed, secret-safe exceptions and validation at provider boundaries.

## Dependencies And Packaging

- Use `uv` and keep `uv.lock` committed.
- Use public PyPI sources only; never commit private package-index URLs.
- Keep runtime dependencies limited to packages imported by production code.
- Pin Docker base images by version and digest; Dependabot owns routine updates.
- Pin GitHub Actions to full commit SHAs with readable release comments.

## Commands

Read `Makefile` before adding or changing commands.

```bash
uv sync --dev --frozen
make check
make docker-check
```

`make check` runs formatting, linting (including Ruff security rules), strict
typing, tests, and package builds. `make docker-check` builds the production
image, exercises authenticated local and broker HTTP MCP handshakes under
hardened runtime flags, and scans the Dockerfile and image.

## Change Validation

- Add focused tests for changed behavior and failure paths.
- Run the relevant targeted tests while iterating.
- Run `make check` after shared, public, dependency, or infrastructure changes.
- Run `make docker-check` after Docker, packaging, dependency, entrypoint, or
  container-security changes.
- Review for blocking async work, resource leaks, broad exception swallowing,
  stale copied configuration, private registry URLs, secrets, and unneeded
  dependencies before finishing.
