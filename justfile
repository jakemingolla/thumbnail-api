# List available recipes
default:
    @just --list

# Install uv if it's not present
[private]
uv:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v uv >/dev/null 2>&1 || curl -LsSf "https://astral.sh/uv/$(cat .uv-version)/install.sh" | sh

# Install dependencies
install: uv
    uv sync --frozen

# Run unit tests
test-unit:
    uv run python -m pytest test/unit/

# Run integration tests
test-integration:
    uv run python -m pytest test/integration/

# Run all tests
test: test-unit test-integration

# Run tests with the 'only' marker
test-only:
    uv run python -m pytest -s -m only test/

# Format code
format:
    uv run ruff format

# Run linters
lint:
    uv run ruff check && uv run ruff format --check && uv run basedpyright

# Run the project in development mode
dev:
    uv run python -m thumbnail_api.main

alias run := dev

# Serve OpenAPI Swagger UI locally (no LocalStack required)
# Open the printed URL in a browser. Ctrl+C to stop.
swagger port="8090":
    #!/usr/bin/env bash
    set -euo pipefail
    cd docs/specification
    echo "Swagger UI: http://127.0.0.1:{{port}}/swagger.html"
    python3 -m http.server "{{port}}"
