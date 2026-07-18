# Pull requests

## Defaults

Open every PR as ready for review (not draft) and enable auto-merge.

1. Open a ready PR (`gh pr create` — do not pass `--draft`).
2. Enable auto-merge immediately after creation, e.g. `gh pr merge --auto --squash` (or the merge method this repo requires).
3. Treat CI (`.github/workflows/`) as the merge gate for baseline correctness — lint, tests, and related checks.

Do not wait for a human to click merge when CI is green.

## Exceptions (rare)

Use a draft PR, or skip/disable auto-merge, only when there is an explicit reason that CI cannot cover:

- Specific human review or approval was requested for this change
- Manual testing outside CI is required before merge
- The change is intentionally held (release timing, coordinated deploy, etc.)

If you open a draft or skip auto-merge, state why in the PR body so the next reader knows it was intentional.

Default bias: ready for review + auto-merge. Drafts and manual holds are the exception, not the norm.
