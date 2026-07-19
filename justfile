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

# Run non-LocalStack integration tests
test-integration:
    uv run python -m pytest test/integration/

# Fast tests only (unit + non-LocalStack integration). LocalStack e2e: `just test-e2e`.
test: test-unit test-integration

# LocalStack e2e harness: Compose → package → terraform apply → pytest test/e2e/
# Slow; requires Docker + Terraform. See docs/agents/local-deploy.md.
test-e2e:
    ./scripts/test-e2e.sh

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

# Allocate unique LocalStack ports/names for this worktree and start
localstack-up:
    ./scripts/localstack-up.sh

# Stop LocalStack and remove containers, volumes, env, and local tfstate
localstack-down:
    ./scripts/localstack-down.sh

# Fail if this worktree still has LocalStack leftovers (required before PR)
localstack-assert-clean:
    ./scripts/localstack-assert-clean.sh

# Serve OpenAPI Swagger UI locally (no LocalStack required)
# Open the printed URL in a browser. Ctrl+C to stop.
swagger port="8090":
    #!/usr/bin/env bash
    set -euo pipefail
    cd docs/specification
    echo "Swagger UI: http://127.0.0.1:{{port}}/swagger.html"
    python3 -m http.server "{{port}}"

# Build Lambda deployment zips (dist/lambda/api.zip + pipeline.zip)
# Idempotent; targets Linux wheels for LocalStack. See docs/agents/local-deploy.md.
package: uv
    ./scripts/package-lambda.sh

# terraform init + apply against this worktree's LocalStack (needs localstack-up + package)
apply:
    ./scripts/terraform-apply.sh

# Print API_BASE and key Terraform outputs (needs prior just apply)
outputs:
    ./scripts/show-outputs.sh

# Happy path: localstack-up → package → apply → outputs (see docs/agents/local-deploy.md)
deploy:
    #!/usr/bin/env bash
    set -euo pipefail
    just localstack-up
    just package
    just apply
    just outputs

# Create job → PUT image → poll until all sizes terminal (needs prior apply)
# Extra flags pass through, e.g. `just upload-watch ./photo.jpg --timeout 180`
upload-watch image *args: uv
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ -f .localstack.env ]]; then
      set -a
      # shellcheck disable=SC1091
      source .localstack.env
      set +a
    fi
    uv run python -m thumbnail_api.cli upload-watch "{{image}}" {{args}}

# Download complete thumbnails for JOB_ID to {size}.jpg (needs prior apply)
# Example: just download-job "$JOB_ID" --out-dir ./thumbs
download-job job_id *args: uv
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ -f .localstack.env ]]; then
      set -a
      # shellcheck disable=SC1091
      source .localstack.env
      set +a
    fi
    uv run python -m thumbnail_api.cli download-job "{{job_id}}" {{args}}
