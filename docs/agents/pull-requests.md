# Pull requests

## Auto-merge (default)

Enable auto-merge on every PR you open.

1. Open the PR (`gh pr create` or equivalent).
2. Enable auto-merge immediately after creation, e.g. `gh pr merge --auto --squash` (or the merge method this repo requires).
3. Treat CI (`.github/workflows/`) as the merge gate for baseline correctness — lint, tests, and related checks.

Do not wait for a human to click merge when CI is green.

## Exceptions (rare)

Skip or disable auto-merge only when there is an explicit reason that CI cannot cover:

- Specific human review or approval was requested for this change
- Manual testing outside CI is required before merge
- The change is intentionally held (release timing, coordinated deploy, etc.)

If you skip auto-merge, state why in the PR body so the next reader knows it was intentional.

Default bias: enable auto-merge. Manual hold is the exception, not the norm.
