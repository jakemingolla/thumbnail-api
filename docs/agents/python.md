# Python style (agents)

How to write Python in this repo so lint/types stay clean without suppressions or defensive noise. Tooling: Ruff (`ALL` minus ignores in `pyproject.toml`), basedpyright strict, pytest via `just test-unit` / `just lint`.

## Prefer redesign over `# noqa`

Do not paper over Ruff/basedpyright with `# noqa` or broad ignore edits when the API can be simpler.

| Smell | Do this instead |
|-------|-----------------|
| Extra parameters only for tests (e.g. `now: str \| None = None`) that trip `PLR0913` (too many args) | Keep production signatures lean. In tests, monkeypatch the clock helper (e.g. `utc_now_iso`) or other seams. |
| `**kwargs: Any` on Protocols / wrappers (`ANN401`) | Use `**kwargs: object` (or name the kwargs you need). Reserve `Any` for true wire bags. |
| `# noqa` to keep a awkward helper signature | Split responsibilities, bundle deps (`client` + `table_name` on a small store type), or drop optional knobs. |

`# noqa` is for genuine exceptions (generated code, third-party shapes), not for test convenience.

## `Any` and casts stay at boundaries

- **OK:** `dict[str, Any]` for DynamoDB AttributeValue maps and other untyped SDK payloads.
- **Avoid:** Field-by-field `cast` / `isinstance` trees when deserializing records **this package wrote**. If the write path owns the shape, deserialize once and cast to the TypedDict / model.
- **Do validate** at true trust boundaries (HTTP bodies, SQS message JSON, untrusted input). Do not re-validate internal round-trips “just in case.”

## AWS clients and fakes

- Type helpers against a small `Protocol` of the boto3 methods you call when unit tests use an in-memory fake. That avoids `cast(BaseClient, fake)` everywhere.
- `botocore.client.BaseClient` is fine for factories that return real clients (`config.clients`); its method return types are often too loose for strict `from_item(...)` call sites.
- Unit tests may fake DynamoDB/SQS/S3 with condition-expression behavior; do not require LocalStack for pure helper logic.

## Time and other seams

- Production code calls a single helper for timestamps (e.g. ISO UTC now).
- Tests freeze that helper via `monkeypatch.setattr` — do not thread `now=` through public APIs.
- Same idea for randomness, uuid generation, and similar: one seam, patch in tests.

## Quick checks before finishing a Python change

- [ ] `just lint` and relevant tests pass with no new `# noqa` unless justified in the diff.
- [ ] No test-only parameters on public functions.
- [ ] No new `Any` outside SDK/wire boundaries.
- [ ] Specs under `docs/specification/` updated if behavior/contracts changed ([`doc-hygiene.md`](doc-hygiene.md)).
