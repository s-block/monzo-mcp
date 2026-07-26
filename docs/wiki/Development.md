# Development

## Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- Docker for container checks
- GNU Make or a compatible `make`
- Node.js for documentation checks

## Setup

```bash
git clone https://github.com/s-block/monzo-mcp.git
cd monzo-mcp
uv sync --dev --frozen
uv run pre-commit install
```

No real Monzo credential is required for the unit test suite or Docker protocol
smoke tests.

## Repository layout

```text
src/monzo_mcp/mcp/       Settings, providers, server, models, and tools
src/monzo_mcp/credentials.py
                         Encrypted local credential persistence
src/monzo_mcp/private_files.py
                         Owner-only secret-file validation
src/monzo_mcp/cli.py     Human OAuth and server entrypoint
tests/                   Mirrored unit, lifecycle, protocol, and Docker tests
docs/wiki/               Versioned operational and user guides
```

The published `aiomonzo` dependency owns the async Monzo client, provider
models, OAuth contracts, transport, and client-level tests.

Keep entrypoints thin and put behavior in the owning package.

## Common commands

```bash
make help
make format
make format-check
make lint
make type-check
make lock-check
make secret-scan
make docs-check
make test
make test-cov
make build
make check
make docker-build
make docker-smoke
make docker-scan
make docker-check
```

`make check` runs:

- Ruff formatting verification;
- Ruff linting, including security rules;
- strict mypy over source and tests;
- public-PyPI-only lockfile validation;
- reviewed-baseline secret detection;
- Markdown documentation validation;
- the complete pytest suite; and
- source and wheel builds.

`make docker-check`:

- builds the production image;
- verifies the runtime user is non-root;
- exercises authenticated local- and broker-mode MCP handshakes under hardened
  runtime flags;
- scans the Dockerfile; and
- scans the built image for high and critical vulnerabilities.

Run `make check` for shared, public, dependency, or infrastructure changes. Run
`make docker-check` for Docker, packaging, dependency, entrypoint, or
container-security changes.

## Design rules

- Preserve full typing and strict Pydantic validation at trust boundaries.
- Keep network and filesystem work non-blocking.
- Reuse the lifecycle-owned `httpx.AsyncClient`.
- Never expose or log credentials or sensitive banking data.
- Keep OAuth, status, and logout human-only.
- Keep writes opt-in and pot movements fail-closed.
- Preserve one local provider and refresh lock for the complete process
  lifetime.
- Keep broker mode free of durable Monzo token state.
- Do not add a database, Redis, webhook listener, stdio transport, or full
  authorization server without an explicit architecture change.

## Tests

Mirror source boundaries under `tests/`. Add focused coverage for:

- success and safe failure mapping;
- malformed or unexpected provider responses;
- secret-file ownership, mode, symlink, and size validation;
- refresh concurrency and atomic token replacement;
- endpoint bearer, Host, and Origin rejection;
- broker delegation and response validation;
- read/write tool registration;
- elicitation rejection and unsupported clients; and
- Docker protocol behavior in both provider modes.

Use synthetic identifiers and transactions only. Real account data and
credentials do not belong in fixtures, snapshots, logs, or screenshots.

## Dependencies

Use `uv` and commit `uv.lock`. Runtime dependencies should be imported by
production code and have a clear contract role. Public PyPI sources only.

The Dockerfile pins base images by version and digest. GitHub Actions should be
pinned to full commit SHAs with readable release comments.

## Pull requests

Before opening a pull request:

1. keep the change focused;
2. update tests and documentation;
3. run targeted checks while iterating;
4. run `make check`;
5. run `make docker-check` when applicable;
6. inspect the complete diff for secrets and personal data; and
7. explain security or compatibility changes clearly.

For a vulnerability, use the repository's private reporting channel instead of
a public issue when possible.

## Release pipeline

CI covers supported Python versions, secret detection, dependency review,
dependency audit, CodeQL, tests, builds, container scans, multi-platform image
publication, SBOM generation, build provenance, and artifact attestation.

Successful builds from `main` publish amd64 and arm64 images to:

```text
ghcr.io/s-block/monzo-mcp
```

Consumers should pin immutable tags or digests rather than relying on `latest`.
