# Job state machine

Normative contract for job and per-size status in DynamoDB. Implementers (create-job, get-job, dispatcher, worker, DynamoDB helpers) must follow these rules. Ambiguity here is a defect — fix this document rather than inventing behavior in handlers.

Related contracts (owned elsewhere):

- HTTP shapes: `docs/specification/api.md` (THUMB-001)
- S3 key layout: `docs/specification/s3-keys.md` (THUMB-003)
- SQS message body and v1 size list: `docs/specification/sqs-messages.md` (THUMB-004)

Status string values in DynamoDB and in the public API must match exactly (lowercase).

---

## Status vocabularies

### Job status

A job’s overall `status` must be one of:

| Status | Meaning |
|--------|---------|
| `pending` | Job record exists; input object not yet accepted by the pipeline (no successful dispatcher fan-out for this job). |
| `processing` | Dispatcher has enqueued work for the configured sizes; at least one size is not terminal. |
| `complete` | Every configured size is `complete`. Terminal. |
| `failed` | At least one configured size is `failed`. Terminal. |

Terminal job statuses are `complete` and `failed`. A terminal job must not transition to any other status.

### Size status

Each entry under `sizes` must have a `status` that is one of:

| Status | Meaning |
|--------|---------|
| `pending` | Size work not yet started by a worker. |
| `processing` | A worker has claimed the size and has not yet recorded a terminal result. |
| `complete` | Thumbnail for this size was written; `output_key` is set. Terminal for the size. |
| `failed` | This size will not succeed; further worker attempts must not change it to `complete`. Terminal for the size. |

Configured sizes for v1 are defined in `docs/specification/sqs-messages.md`. Every job item must include exactly those size keys at creation time, each initialized to `pending`.

---

## Actors and responsibilities

| Actor | May change |
|-------|------------|
| Create-job handler | Create job item: overall `pending`, all sizes `pending`. Must not enqueue work. |
| Dispatcher | Overall `pending` → `processing` after fan-out. Must not set size statuses to terminal. |
| Worker | Per-size `pending` → `processing` → `complete` or `failed`; then apply job rollup. |
| Get-job handler | Read only. |

No other writer may invent statuses outside the tables above.

---

## Legal transitions

### Job-level

```text
pending ──(dispatcher fan-out succeeds)──► processing
processing ──(rollup: all sizes complete)──► complete
processing ──(rollup: any size failed)──► failed
```

Illegal (must not occur):

- `pending` → `complete` or `failed` (skipping `processing`)
- `complete` → any other status
- `failed` → any other status
- `processing` → `pending`
- Any transition not listed above

**`pending` → `processing`:** The dispatcher must set overall status to `processing` after it has successfully sent one SQS message per configured size for the job (or determined that equivalent messages for this fan-out are already present — see [Idempotency](#idempotency)). The transition must use a condition so that:

- From `pending`, the update succeeds and becomes `processing`.
- If the item is already `processing`, `complete`, or `failed`, the dispatcher must not regress status (treat as success / no-op for status).

**`processing` → `complete`:** Must occur only via [rollup](#job-status-rollup) when every size is `complete`.

**`processing` → `failed`:** Must occur only via rollup when any size is `failed`. Partial success (some sizes `complete`, others `failed`) still yields overall `failed`.

### Size-level

```text
pending ──(worker claims message)──► processing
processing ──(thumbnail written)──► complete
processing ──(permanent failure or exhausted retries)──► failed
pending ──(permanent failure before claim, optional)──► failed
```

Illegal (must not occur):

- `complete` → any other status
- `failed` → any other status
- `complete` ↔ `failed`
- `processing` → `pending`

**Claim:** When a worker begins work for a size, it must set that size to `processing` if the current size status is `pending`. If the size is already `processing`, the worker may proceed (at-least-once delivery). If the size is already `complete` or `failed`, the worker must not change that size’s status (see [Idempotency](#idempotency)).

**Complete:** After a successful PutObject to the size’s output key (per `s3-keys.md`), the worker must set that size to `complete` and set `output_key` to that key. The update must be conditioned so it does not overwrite a size that is already `failed`.

**Fail:** See [Retries and DLQ](#retries-and-dlq).

---

## Job status rollup

Overall status is a pure function of per-size statuses once the job has left `pending`:

1. If **any** configured size is `failed` → overall must be `failed`.
2. Else if **every** configured size is `complete` → overall must be `complete`.
3. Else → overall must remain `processing` (mix of `pending` / `processing` / `complete`, with no `failed`).

While overall is still `pending`, size statuses must all remain `pending` (workers must not run before dispatcher fan-out for that job). Rollup rules (1)–(3) apply after the dispatcher has moved the job to `processing`.

After each size terminal update (`complete` or `failed`), the writer must recompute and persist overall status according to the rules above in the same logical update (single DynamoDB request preferred; if multiple requests are used, the size update must land before a subsequent rollup read observes a stale size map).

Clients polling `GET /jobs/{job_id}` must treat `complete` and `failed` as done; they must not assume all `output_key` values are present when `status` is `failed`.

---

## Retries and DLQ

SQS provides at-least-once delivery. A redrive policy moves a message to the DLQ after `maxReceiveCount` receives.

### Concrete queue settings (v1)

| Setting | Value | Where configured |
|---------|-------|------------------|
| Work queue name | `{name_prefix}-work` (default `thumbnail-work`) | Terraform `infra/sqs.tf` |
| DLQ name | `{name_prefix}-work-dlq` (default `thumbnail-work-dlq`) | Terraform `infra/sqs.tf` |
| `maxReceiveCount` | **5** | Terraform `var.sqs_max_receive_count` (default 5) |

**`maxReceiveCount = 5`:** A message is redriven to the DLQ after five receives without a successful delete. That gives workers four transient retries after the first delivery, then a final attempt on the fifth receive. On that fifth receive (`ApproximateReceiveCount >= 5`), the worker must treat the attempt as exhausted retries per [Exhausted retries (redrive)](#exhausted-retries-redrive) — mark the size `failed`, apply rollup, and allow the invocation to fail so SQS redrives the message.

Changing `maxReceiveCount` is a contract change: update this table and the Terraform variable default together.

Visibility timeout and retention are Terraform operational knobs (`var.sqs_visibility_timeout_seconds`, etc.); they are not part of the status contract, but visibility timeout must remain long enough for a single worker invocation.

### Transient vs permanent errors

| Class | Examples | Worker must |
|-------|----------|-------------|
| Transient | Throttling, temporary S3/DynamoDB errors, unexpected infra blips | Fail the invocation **without** marking the size `failed`, so SQS retries the message. |
| Permanent | Unreadable/corrupt image, unsupported format, missing input object that will not appear, message schema violation for a required field | Mark the size `failed` (and apply job rollup), then **delete** the SQS message (acknowledge) so it does not retry or DLQ for the same logical failure. |

### Exhausted retries (redrive)

When a message’s approximate receive count has reached `maxReceiveCount` (**5** in v1; last attempt before redrive), and the worker still cannot complete the size:

1. The worker must set that size to `failed`.
2. The worker must apply job rollup (overall becomes `failed` if not already terminal).
3. The worker then must allow the message to fail the invocation (or otherwise follow the queue’s redrive path) so operators can inspect the DLQ payload.

A size must become `failed` no later than this last attempt. Relying solely on a DLQ consumer to update DynamoDB is out of scope for v1; the worker is responsible for the DynamoDB failure write.

### Job-level failure timing

- A job becomes `failed` as soon as any size is recorded `failed` (rollup), even if other sizes are still `pending` or `processing`.
- Remaining in-flight sizes may still reach `complete` or `failed` afterward; overall must stay `failed` once any size has failed (terminal).
- Workers updating a non-failed size after overall is already `failed` must still persist that size’s terminal result; they must not set overall back to `processing` or `complete`.

---

## Idempotency

Duplicate S3 notifications and duplicate SQS deliveries must not corrupt job state.

### Duplicate S3 → dispatcher

On a duplicate `ObjectCreated` for an input key the dispatcher already handled:

1. It must not create a new job.
2. It must not move a terminal job (`complete` / `failed`) back to `processing` or `pending`.
3. Re-sending SQS messages for sizes that are still `pending` or `processing` is allowed (at-least-once). Re-sending for sizes already `complete` or `failed` is allowed only if workers treat those messages as no-ops (below).
4. Overall `pending` → `processing` must remain safe under retries (condition expression / equivalent).

### Duplicate SQS → worker

1. Output objects for a size must use the deterministic key from `s3-keys.md`. Retries may overwrite the same key with equivalent bytes.
2. If the size is already `complete`, the worker must leave status and `output_key` unchanged (optional: verify object exists; must not set `failed`).
3. If the size is already `failed`, the worker must leave it `failed` (must not set `complete` from a later delivery).
4. Size status updates must use conditions (or equivalent compare-and-set) so illegal transitions in this document are rejected rather than applied.
5. Job rollup after a no-op size update must still obey terminal overall status (never leave `failed` / `complete`).

### Conditional write outcomes (shared helpers)

Shared DynamoDB helpers (and any equivalent writer) must use condition expressions so rejected illegal transitions do not mutate the item. The following condition failures are **success / no-op** for the caller (not errors), provided the item still exists:

| Write | Condition (conceptual) | No-op when |
|-------|------------------------|------------|
| Create job | `attribute_not_exists(job_id)` | — (duplicate `job_id` is an error: do not overwrite) |
| Overall `pending` → `processing` | `#status = pending` | Status is already `processing`, `complete`, or `failed` |
| Size claim → `processing` | `sizes.<size>.status = pending` | Size is already `processing`, `complete`, or `failed` |
| Size → `complete` + `output_key` | `sizes.<size>.status = processing` | Size is already `complete` (leave `output_key`) or `failed` (must not overwrite) |
| Size → `failed` | `sizes.<size>.status IN (pending, processing)` | Size is already `failed` or `complete` (must not overwrite `complete`) |

Rollup of overall status after a size becomes terminal must not move a job out of `complete` or `failed`. Prefer `ReturnValues=ALL_NEW` on the size update, then a separate conditional overall update with `#status = processing` when the computed rollup differs.

### Create-job

`job_id` must be unique per create. A client retry that generates a new `job_id` creates a distinct job; that is not a duplicate delivery of the same job. Put-item for create must use `attribute_not_exists(job_id)` (or equivalent) so a reused id cannot clobber an existing record.

---

## DynamoDB item shape

Partition key: `job_id` (string). No sort key in v1. No GSI/TTL/streams required by this contract.

Handlers must persist items that conform to this sketch (attribute names and nesting are normative; example values are illustrative):

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "pending",
  "input_key": "uploads/a1b2c3d4-e5f6-7890-abcd-ef1234567890/original",
  "sizes": {
    "128": {
      "status": "pending",
      "output_key": null
    },
    "256": {
      "status": "pending",
      "output_key": null
    },
    "512": {
      "status": "pending",
      "output_key": null
    }
  },
  "created_at": "2026-07-18T22:00:00.000Z",
  "updated_at": "2026-07-18T22:00:00.000Z"
}
```

### Field rules

| Field | Type | Rules |
|-------|------|--------|
| `job_id` | string (UUID) | Partition key. Immutable after create. |
| `status` | string | Job status vocabulary above. |
| `input_key` | string | Input object key per `s3-keys.md`. Immutable after create. |
| `sizes` | map keyed by size string | Keys must be the decimal string form of each configured size (e.g. `"128"`). Must include every configured size. |
| `sizes.<size>.status` | string | Size status vocabulary above. |
| `sizes.<size>.output_key` | string or null | Must be null unless size status is `complete`. When `complete`, must be the output key written for that size. |
| `created_at` | string | ISO-8601 UTC timestamp set at create. Immutable. |
| `updated_at` | string | ISO-8601 UTC timestamp; must be bumped on every successful status-changing write. |

Optional attribute (should, not must, for v1): `sizes.<size>.error` — short string reason when status is `failed`. If present, get-job may surface it; absence must not break clients.

Concrete size key set in examples (`128` / `256` / `512`) must match the v1 list in `sqs-messages.md`.

### Example: processing with mixed sizes

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "processing",
  "input_key": "uploads/a1b2c3d4-e5f6-7890-abcd-ef1234567890/original",
  "sizes": {
    "128": {
      "status": "complete",
      "output_key": "thumbnails/a1b2c3d4-e5f6-7890-abcd-ef1234567890/128.jpg"
    },
    "256": {
      "status": "processing",
      "output_key": null
    },
    "512": {
      "status": "pending",
      "output_key": null
    }
  },
  "created_at": "2026-07-18T22:00:00.000Z",
  "updated_at": "2026-07-18T22:00:05.000Z"
}
```

### Example: failed job (partial success)

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "failed",
  "input_key": "uploads/a1b2c3d4-e5f6-7890-abcd-ef1234567890/original",
  "sizes": {
    "128": {
      "status": "complete",
      "output_key": "thumbnails/a1b2c3d4-e5f6-7890-abcd-ef1234567890/128.jpg"
    },
    "256": {
      "status": "failed",
      "output_key": null
    },
    "512": {
      "status": "pending",
      "output_key": null
    }
  },
  "created_at": "2026-07-18T22:00:00.000Z",
  "updated_at": "2026-07-18T22:00:12.000Z"
}
```

---

## Invariants (summary)

1. Overall `complete` iff every size is `complete`.
2. Overall `failed` if any size is `failed` (and the job has left `pending`).
3. Terminal statuses never regress.
4. Duplicate S3/SQS deliveries must not flip `complete` ↔ `failed` or clear a successful `output_key`.
5. DynamoDB is the source of truth for polling; workers and get-job must agree by reading/writing only this shape and these transitions.

---

## Out of scope (v1)

- SNS / EventBridge completion notifications
- TTL, archival, or stuck-`pending` cleanup when the client never uploads
- Separate DLQ consumer Lambda
- Changing configured sizes on an in-flight job
