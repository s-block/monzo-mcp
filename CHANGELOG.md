# Changelog

## Unreleased

### Added

- PyPI-backed `aiomonzo` integration for fully asynchronous, typed Monzo API
  access.
- HTTP-only FastMCP Docker service with data-minimized read tools, endpoint
  bearer authentication, and exact Host/Origin validation.
- Generic `AccessTokenProvider` boundary with encrypted local OAuth and
  request-scoped credential-broker implementations.
- Opt-in mutation tools with fail-closed human confirmation for pot movements.
- Human-only OAuth login, status, and logout CLI.
- AES-256-GCM encrypted credential persistence for the default single-tenant
  server and human-only OAuth workflow.
- Hardened non-root Docker image and authenticated local/broker HTTP MCP protocol
  smoke tests.
- Minimal, digest-pinned production image with a deny-by-default build context.
- GHCR publishing for amd64 and arm64 images with SBOM and provenance
  attestations.
- Dependency, source, workflow, Dockerfile, and container vulnerability checks.
- Bounded fixed and streamed HTTP request bodies, Uvicorn concurrency, and
  model-facing string and metadata inputs.
- Sanitized security-event logging for authentication, transport, broker, and
  provider failures.
- CI-enforced secret and public-package-registry checks, a documented Markdown
  policy, an actionable security policy, and complete public package metadata.
- Contributor guidance, a code of conduct, structured issue forms, and a pull
  request checklist.

### Changed

- Read-only operation uses stateless JSON responses; enabling elicitation-backed
  write tools selects stateful Streamable HTTP.
- Transaction lists default to a 30-day, 30-entry window, return compact list
  items and pagination metadata, and retain explicit date/cursor/limit
  overrides.
- The Docker service defaults to single-tenant local credentials and owns
  refresh and atomic rotation. Existing stateless multi-user behavior remains
  available with `MONZO_MCP_ACCESS_TOKEN_PROVIDER=broker`.
- Removed copied Docker Compose maintenance, generic ignore rules, private
  package-index metadata, and unrelated project guidance.
- Kept project documentation versioned in the repository instead of linking to
  an unpublished GitHub Wiki.
- Allowed verified private-repository builds to publish GHCR images while still
  requiring successful CodeQL analysis once the repository is public.
