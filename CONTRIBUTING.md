# Contributing

Thank you for contributing to `monzo-mcp`. Changes should keep this banking
integration typed, asynchronous, minimal, and safe with sensitive data.

By participating, you agree to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Before opening an issue

- Search existing issues first.
- Use synthetic identifiers and transaction data.
- Never include credentials, tokens, account details, real transactions, or
  screenshots of financial information.
- Report suspected vulnerabilities through the private process in
  [`SECURITY.md`](SECURITY.md), not through a public issue.

## Development setup

The project supports Python 3.12 through 3.14 and uses
[uv](https://docs.astral.sh/uv/) for dependency management. The complete checks
also require Node.js for Markdown validation and Docker for container
validation.

```bash
git clone https://github.com/s-block/monzo-mcp.git
cd monzo-mcp
uv sync --dev --frozen
uv run pre-commit install
```

No real Monzo credentials are required for the tests.

## Making changes

- Keep changes focused and preserve existing public contracts unless a change
  is necessary and documented.
- Fully type new and changed Python.
- Keep network paths asynchronous and preserve cancellation.
- Add focused tests for new behavior and failure paths.
- Update the versioned documentation when behavior or configuration changes.
- Do not add real credentials or financial data to fixtures, logs,
  screenshots, documentation, commits, or issue discussions.
- Pin Docker base images by version and digest and GitHub Actions by full commit
  SHA.

See the [development guide](docs/wiki/Development.md) for the architecture,
security invariants, tests, and release pipeline.

## Validation

Run the complete local checks:

```bash
make check
```

For Docker, packaging, dependency, entrypoint, or container-security changes,
also run:

```bash
make docker-check
```

State which checks were run in the pull request. Explain any check that could
not be run.

## Pull requests

Pull requests should:

- explain the problem and the chosen solution;
- identify security, privacy, compatibility, or operational effects;
- include relevant tests and documentation;
- keep generated and unrelated changes out of the diff; and
- pass all required checks.

Maintainers may ask for changes to keep the service within its documented
personal, self-hosted use case and security model.
