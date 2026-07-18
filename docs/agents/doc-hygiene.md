# Doc hygiene

Keep `docs/` in lockstep with implementation. Specs and agent notes are decisions and guardrails, not write-once artifacts. Drift is a bug.

Audience tone and language live in [`docs/README.md`](../README.md). This note is process only — not a fourth audience.

## Specs are decisions

- `docs/specification/` holds locked choices and invariants. Implement against them; do not invent around them.
- Ambiguity in a spec is a defect: fix the spec (same change or before coding), then implement.
- Normative rules (`must` / `should`) belong in specs, not only in tickets or PR bodies.

## Spec-first when contracts change

When a ticket would change a contract (API, state, keys, queue shapes, invariants):

1. Update the matching `docs/specification/` doc first, or in the same PR as the code.
2. Implement against that updated spec.
3. Do not land code that contradicts `docs/specification/`.

## Change type → doc set

| If you change… | Update |
|----------------|--------|
| API shapes, status codes, auth, error contracts | `docs/specification/` |
| Job/state machine, transitions, terminal states | `docs/specification/` |
| S3 key layout, object metadata conventions | `docs/specification/` |
| SQS/queue message schema or semantics | `docs/specification/` |
| Other behavior or invariants callers rely on | `docs/specification/` |
| Deploy, verify, navigate, or operate commands/paths | `docs/agents/` |
| Onboarding, why/how-to-run for humans | `docs/human/` |

If unsure which set: prefer `docs/specification/` for anything normative; `docs/agents/` for imperative how-to; `docs/human/` for skimmable orientation. See [`docs/README.md`](../README.md).

## Do not

- Leave “temporary” undocumented behavior that callers or operators must know.
- Duplicate normative rules only in ticket text or PR bodies — put them in `docs/specification/`.
- Invent API, state, key, or message shapes that contradict `docs/specification/`.
- Update code/verify steps without updating the matching `docs/` set in the same change.
- Put critical guidance only in IDE/vendor rule files — see [`ide-rules.md`](ide-rules.md).

## Definition of done (doc hygiene)

Before calling a change done:

- [ ] Behavior, contracts, or verify steps that changed have matching updates under `docs/` (same change / PR).
- [ ] Contract changes: `docs/specification/` updated before or with the implementation.
- [ ] Deploy/verify/command changes: `docs/agents/` updated.
- [ ] Human onboarding/run-flow changes: `docs/human/` updated.
- [ ] No normative rule exists only in a ticket or PR body.
- [ ] No undocumented “temporary” behavior left for the next agent to rediscover.
- [ ] Tone matches the audience in [`docs/README.md`](../README.md).
