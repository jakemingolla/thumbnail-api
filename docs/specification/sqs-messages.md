# SQS message schema

Normative contract for the JSON body the dispatcher enqueues and the worker consumes. One S3 upload fans out to **N** messages — one per configured thumbnail size. Ambiguity here is a defect: fix this document rather than inventing fields in handlers.

Related contracts (owned elsewhere):

- S3 key layout: `docs/specification/s3-keys.md` (THUMB-003)
- Job / size status, retries, and DLQ failure semantics: `docs/specification/job-state-machine.md` (THUMB-002)

## Transport

| Concern | Requirement |
|---------|-------------|
| Queue | Single work queue (URL/ARN from config / Terraform). |
| Body encoding | UTF-8 JSON object in the SQS `MessageBody`. |
| Fan-out | Dispatcher must send exactly one message per configured size for the job. |
| Worker batch size | Event source mapping **batch size must be 1** initially (one SQS record per Lambda invocation). Larger batches are out of scope until a later ticket revises this document. |

Visibility timeout is a Terraform operational knob (`infra/sqs.tf`). Concrete **`maxReceiveCount` is 5** (v1); failure / redrive semantics (when a size or job becomes `failed`, transient vs permanent, last-receive behavior) are normative only in `job-state-machine.md` — do not duplicate the full rules here.

## Message body

Every message must be a JSON object with exactly these required fields (additional fields must be ignored by consumers; producers must not rely on them in v1):

| Field | JSON type | Rules |
|-------|-----------|--------|
| `job_id` | string | Non-empty. Must identify the job. Must not contain `/`. Must match the `job_id` embedded in `input_key`. |
| `input_key` | string | Must be the input object key for that job per `s3-keys.md`: `uploads/{job_id}/original`. |
| `size` | number (integer) | Must be one of the [configured sizes](#configured-sizes-v1). Must be a JSON number, not a string. |

No other field is required in v1. Workers must derive the output key from `job_id` and `size` using `s3-keys.md` (`thumbnails/{job_id}/{size}.jpg`, with `{size}` as the decimal string form of the integer).

### Example

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "input_key": "uploads/550e8400-e29b-41d4-a716-446655440000/original",
  "size": 256
}
```

Corresponding fan-out for one upload (three messages; only `size` differs):

```json
{"job_id":"550e8400-e29b-41d4-a716-446655440000","input_key":"uploads/550e8400-e29b-41d4-a716-446655440000/original","size":128}
{"job_id":"550e8400-e29b-41d4-a716-446655440000","input_key":"uploads/550e8400-e29b-41d4-a716-446655440000/original","size":256}
{"job_id":"550e8400-e29b-41d4-a716-446655440000","input_key":"uploads/550e8400-e29b-41d4-a716-446655440000/original","size":512}
```

### Size string form

Wherever a size is used as an S3 key segment or DynamoDB map key, producers and consumers must use the decimal string of the integer with no sign, spaces, or leading zeros (`128` → `"128"`). That string form must match the keys under `sizes` in `job-state-machine.md`.

## Configured sizes (v1)

The configured thumbnail sizes for v1 **must** be exactly:

| `size` (pixels) | Meaning |
|-----------------|---------|
| `128` | Longest edge 128 px |
| `256` | Longest edge 256 px |
| `512` | Longest edge 512 px |

Rules:

- Create-job, dispatcher, and worker must all use this same set (via shared config that defaults to these values).
- Dispatcher must enqueue one message for each entry in this set — no more, no fewer — for a successful fan-out.
- A message whose `size` is not in this set is invalid (see below).
- Changing the set is a contract change: update this document, job records, and config together.

Resize algorithm details (fit, crop, quality) are owned by the image-resize implementation ticket; this document only defines which size integers exist.

## Validation and malformed messages

The worker must validate each record before side effects (other than safe idempotent reads).

A message is **malformed** if any of the following hold:

- `MessageBody` is not valid JSON
- Body is not a JSON object
- Any required field is missing
- `job_id` is not a non-empty string, or contains `/`
- `input_key` is not a string, or does not equal `uploads/{job_id}/original` for the message’s `job_id`
- `size` is not a JSON integer in the configured size set (wrong type, non-integer number, or unknown value)

### Handling (fail, do not skip)

| Case | Worker must |
|------|-------------|
| Malformed (cannot trust `job_id` / `size`) | **Fail** the invocation: do **not** delete/acknowledge the message, and do **not** update DynamoDB. SQS must retry; after redrive exhaustion the message lands on the DLQ. |
| Well-formed body, processing error | Follow transient vs permanent rules in `job-state-machine.md` (retry vs mark size `failed` and acknowledge). |

Workers must **not** silently skip (acknowledge/delete) malformed messages. Skipping would hide poison messages and leave jobs stuck in `processing` with no DLQ signal.

Dispatcher must only enqueue messages that satisfy this schema for the configured size set.

## Out of scope

- Changing `maxReceiveCount` / queue names without updating `job-state-machine.md` and Terraform together
- SQS message attributes, FIFO queues, or content-based deduplication
- Batch sizes greater than 1
- Partial fan-out or priority queues
