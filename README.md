# python-template

A minimal Python project template with [uv](https://docs.astral.sh/uv/), strict typing (basedpyright), linting (ruff), and pytest.

Application code lives in the installable package `python_template` under `src/python_template/`. After `just install` (`uv sync`), imports resolve like a normal package (`import python_template`).

## Prerequisites

- **Python 3.13.5** — match `.python-version` (pyenv or similar is recommended)
- **[just](https://github.com/casey/just)** — command runner for common workflows (`brew install just`, or see [installation](https://github.com/casey/just#installation))
- **uv** — not required upfront; `just install` installs the pinned version from `.uv-version` if uv is missing

## Quick start

Clone the repo, install dependencies, and run tests:

```bash
git clone <repo-url>
cd python-template
just install
just test
```

`just install` runs `uv sync --frozen`, which installs the project in editable/dev mode along with its dependencies. Run `just` with no arguments to list all recipes. Tests do not require a `.env` file.

## Configuration (`.env`)

Runtime settings live in a `.env` file at the project root (gitignored). Values are loaded by [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) via `get_config()` — see `src/python_template/config/types.py` for the schema and `src/python_template/config/main.py` for the factory.

Create `.env` before running `just dev`:

```dotenv
OPENAI_API_KEY=your-key-here
# optional; defaults to gpt-4o-mini
DEFAULT_MODEL=gpt-4o-mini
```

Environment variable names use uppercase with underscores; they map to the snake_case fields on `Config` (e.g. `openai_api_key` → `OPENAI_API_KEY`).

## Templating a new project

Rename the package in one pass so the directory, imports, project metadata, and entrypoint stay aligned:

1. Rename `src/python_template/` to `src/<your_package>/` (import name: letters, numbers, underscores).
2. Set `project.name` in `pyproject.toml` to the distribution name (hyphens OK; it should normalize to the same import name).
3. Point `[tool.hatch.build.targets.wheel].packages` at `src/<your_package>`.
4. Update the `just dev` module path (`python -m <your_package>.main`) and any `import` / `from` lines that still say `python_template`.
5. Refresh `README.md` (title, paths, and this section) for the new name.

Also update `description` and application logic in `src/<your_package>/main.py` and `src/<your_package>/config/types.py` as needed.

Keep the justfile, lint/type-check config, and test layout unless your project needs different tooling.
