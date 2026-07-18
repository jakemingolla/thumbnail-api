# Pull requests

## Defaults

Open every PR as ready for review (not draft) and enable auto-merge.

## Before `gh pr create` (required)

LocalStack / Docker leftovers bloat the host. **Must** tear down this worktree’s instance and prove it is clean before opening a PR (lifecycle details: [`dev-lifecycle.md`](dev-lifecycle.md)):

```bash
just localstack-down
just localstack-assert-clean
```

Do **not** run `gh pr create` if `just localstack-assert-clean` fails.

`localstack-assert-clean` only checks **this worktree** (env file, volume dirs, local Terraform state). Other agents’ running instances on the same machine are left alone.

If you never started LocalStack, still run both commands — `localstack-down` is idempotent and `localstack-assert-clean` confirms a clean tree.

## Create + auto-merge

1. Run the cleanup commands above.
2. Open a ready PR (`gh pr create` — do not pass `--draft`).
3. Enable auto-merge immediately after creation, e.g. `gh pr merge --auto --squash` (or the merge method this repo requires).
4. Treat CI (`.github/workflows/`) as the merge gate for baseline correctness — lint, tests, and related checks.

Do not wait for a human to click merge when CI is green.

## Exceptions (rare)

Use a draft PR, or skip/disable auto-merge, only when there is an explicit reason that CI cannot cover:

- Specific human review or approval was requested for this change
- Manual testing outside CI is required before merge
- The change is intentionally held (release timing, coordinated deploy, etc.)

If you open a draft or skip auto-merge, state why in the PR body so the next reader knows it was intentional.

**Cleanup is never optional.** Draft / no-auto-merge exceptions do not skip `just localstack-down` + `just localstack-assert-clean`.

Default bias: ready for review + auto-merge. Drafts and manual holds are the exception, not the norm.
