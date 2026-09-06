FROM ghcr.io/astral-sh/uv:0.12.10@sha256:2bb3ebca0a796a155094a27773d290c4b074572e6107f171d88d086682fd2500 AS uv

FROM python:3.13.14-alpine3.23@sha256:9fdbf2e3e82628351513560b121e2ee6ce31cac212be9e070c5a5e2769fb5e76 AS builder

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/

RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13.14-alpine3.23@sha256:9fdbf2e3e82628351513560b121e2ee6ce31cac212be9e070c5a5e2769fb5e76 AS runtime

LABEL org.opencontainers.image.source="https://github.com/s-block/monzo-mcp" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="0.1.0"

RUN mkdir -p /run/secrets \
    && chown 10001:10001 /run/secrets

WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 10001:10001

EXPOSE 8000

ENTRYPOINT ["/app/.venv/bin/monzo-mcp"]
CMD ["serve"]
