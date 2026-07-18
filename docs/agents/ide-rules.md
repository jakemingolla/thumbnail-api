# IDE / vendor rules

Critical agent guidance lives under `docs/`. Vendor-specific rule directories (e.g. `.cursor/rules/`) must stay thin pointers so the repo stays usable outside any one IDE.

## Rules

- Put normative process, contracts, commands, and checklists in `docs/` (`agents/`, `specification/`, or `human/` per [`docs/README.md`](../README.md)).
- IDE rule files must contain **no critical information** — only a short pointer to the canonical `docs/` path (plus frontmatter the tool needs).
- When adding or changing agent guardrails: update the `docs/` note first, then add or adjust the vendor pointer if the tool needs one.
- Do not duplicate substantive guidance in `.cursor/rules/`, Copilot instructions, or other vendor files.

## Pointer file shape (Cursor example)

```markdown
---
description: One-line what to load
alwaysApply: true
---

Follow `docs/agents/<canonical-note>.md`.
```

Same idea for any other vendor: point at `docs/`, do not restate it.
