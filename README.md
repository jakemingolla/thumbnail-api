# thumbnail-api

A Python project with [uv](https://docs.astral.sh/uv/), strict typing (basedpyright), linting (ruff), and pytest.

Application code lives in the installable package `thumbnail_api` under `src/thumbnail_api/`. After `just install` (`uv sync`), imports resolve like a normal package (`import thumbnail_api`).

## Prerequisites

- **Python 3.13.5** — match `.python-version` (pyenv or similar is recommended)
- **[just](https://github.com/casey/just)** — command runner for common workflows (`brew install just`, or see [installation](https://github.com/casey/just#installation))
- **uv** — not required upfront; `just install` installs the pinned version from `.uv-version` if uv is missing

## Quick start

Clone the repo, install dependencies, and run tests:

```bash
git clone <repo-url>
cd thumbnail-api
just install
just test
```

`just install` runs `uv sync --frozen`, which installs the project in editable/dev mode along with its dependencies. Run `just` with no arguments to list all recipes. Tests do not require a `.env` file.

## Configuration (`.env`)

Runtime settings live in a `.env` file at the project root (gitignored). Values are loaded by [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) via `get_config()` — see `src/thumbnail_api/config/types.py` for the schema and `src/thumbnail_api/config/main.py` for the factory.

Create `.env` before running `just dev`:

```dotenv
environment=development
```

Environment variable names use uppercase with underscores; they map to the snake_case fields on `Config` (e.g. `environment` → `ENVIRONMENT`).
