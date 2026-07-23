.PHONY: help install install-dev pre-commit-install format format-check lint type-check test test-cov build check docker-build docker-smoke docker-scan docker-check _docker-smoke _docker-scan clean
.DEFAULT_GOAL := help

IMAGE ?= monzo-mcp:local
TRIVY_IMAGE ?= aquasec/trivy:0.72.0@sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(firstword $(MAKEFILE_LIST)) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

install: ## Install locked production dependencies
	uv sync --no-dev --frozen

install-dev: ## Install locked development dependencies
	uv sync --dev --frozen

pre-commit-install: ## Install the repository pre-commit hooks
	uv run pre-commit install

format: ## Apply Ruff formatting and safe lint fixes
	uv run ruff check --fix .
	uv run ruff format .

format-check: ## Check formatting without changing files
	uv run ruff format --check .

lint: ## Run Ruff lint checks
	uv run ruff check .

type-check: ## Type-check source and tests
	uv run mypy src/monzo_mcp tests

test: ## Run the test suite
	uv run pytest -v

test-cov: ## Run tests with terminal coverage
	uv run coverage run -m pytest -v
	uv run coverage report --show-missing

build: ## Build source and wheel distributions
	uv build

check: format-check lint type-check test build ## Run all required checks

docker-build: ## Build the hardened local MCP image
	docker build --tag $(IMAGE) .

_docker-smoke:
	test "$$(docker run --rm --entrypoint id $(IMAGE) -u)" != "0"
	uv run python -m tests.mcp.docker_http_smoke $(IMAGE)

docker-smoke: docker-build _docker-smoke ## Verify image user and HTTP MCP handshake

_docker-scan:
	docker run --rm --read-only --cap-drop=ALL --security-opt=no-new-privileges \
		--user "$$(id -u):$$(id -g)" \
		--env HOME=/tmp \
		--tmpfs /tmp:rw,noexec,nosuid,size=2147483648,mode=1777 \
		--mount "type=bind,source=$(CURDIR)/Dockerfile,target=/Dockerfile,readonly" \
		$(TRIVY_IMAGE) config \
		--exit-code 1 \
		--severity HIGH,CRITICAL \
		/Dockerfile
	monzo_image_archive="$$(mktemp "$${TMPDIR:-/tmp}/monzo-mcp-image.XXXXXX.tar")"; \
		trap 'rm -f "$$monzo_image_archive"' EXIT INT TERM; \
		docker save --output "$$monzo_image_archive" $(IMAGE); \
		docker run --rm --read-only --cap-drop=ALL \
			--security-opt=no-new-privileges \
			--user "$$(id -u):$$(id -g)" \
			--env HOME=/tmp \
			--tmpfs /tmp:rw,noexec,nosuid,size=2147483648,mode=1777 \
			--mount "type=bind,source=$$monzo_image_archive,target=/image.tar,readonly" \
			$(TRIVY_IMAGE) image \
			--cache-dir /tmp/trivy-cache \
			--exit-code 1 \
			--ignore-unfixed \
			--scanners vuln \
			--severity HIGH,CRITICAL \
			--input /image.tar

docker-scan: docker-build _docker-scan ## Scan Dockerfile and image for serious findings

docker-check: docker-build _docker-smoke _docker-scan ## Run all container checks

clean: ## Remove generated local build and test artifacts
	uv run python -c "import shutil; [shutil.rmtree(path, ignore_errors=True) for path in ('build', 'dist', '.mypy_cache', '.pytest_cache', '.ruff_cache', 'htmlcov')]"
