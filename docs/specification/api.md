# Public HTTP API (v1)

Normative contract for the thumbnail-api JSON surface. Implementers (human or agent) **must** treat this document as the source of truth for request/response shapes and status codes. [`openapi.yaml`](openapi.yaml) **must** stay consistent with this file; when they disagree, fix the mismatch in the same change.

Language: **must** = required; **should** = strongly preferred unless a documented exception applies.

## Scope

### In scope (v1)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/jobs` | Create a job; return a presigned S3 upload URL |
| `GET` | `/jobs/{job_id}` | Read job status for client polling |

The API **must** be JSON-only. Image bytes **must not** pass through API Gateway or the API Lambdas; clients **must** upload via the presigned URL returned by `POST /jobs`.

### Out of scope (v1)

The following **must not** be implemented or documented as v1 endpoints:

- Auth, API keys, Cognito, or authorizers
- Rate limits or pagination
- Listing jobs
- Presigned GET URLs for completed thumbnails (e.g. optional later `GET /jobs/{job_id}/urls`)
- Binary media types / body passthrough for images on the API

## Conventions

- Paths are relative to the deployed API base URL (stage included). Example LocalStack base: `http://localhost:4566/_aws/execute-api/<apiId>/dev`.
- JSON field names **must** use `snake_case`.
- Timestamps **must** be UTC ISO-8601 strings with a `Z` suffix (e.g. `2026-07-18T22:00:00Z`).
- `job_id` **must** be a UUID string (8-4-4-4-12 hex).
- Overall job `status` and per-size `status` values **must** be lowercase strings from the sets defined below. Transition rules live in the job state-machine spec; this document only defines the wire shapes clients see.

## Error responses

Unless noted otherwise, 4xx/5xx responses from the jobs API **must** use `Content-Type: application/json` and a body of:

```json
{
  "error": {
    "code": "string",
    "message": "string"
  }
}
```

| Field | Rule |
|-------|------|
| `error` | **must** be an object |
| `error.code` | **must** be a stable machine-readable snake_case string (see endpoints) |
| `error.message` | **must** be a human-readable explanation; **should** be safe to log |

## `POST /jobs`

Creates a job in `pending` status and returns a presigned PUT URL for the input object.

### Request

| Rule | Requirement |
|------|-------------|
| Method / path | **must** be `POST /jobs` |
| `Content-Type` | **must** be `application/json` |
| Body | **must** be a JSON object |

| Field | Type | Rule |
|-------|------|------|
| `content_type` | string | **must** be present; **must** be one of `image/jpeg`, `image/png`, `image/webp` |

Unknown fields in the body **must** cause a `400` response (`code`: `invalid_request`).

### Success response

| Rule | Requirement |
|------|-------------|
| Status | **must** be `201` |
| `Content-Type` | **must** be `application/json` |

| Field | Type | Rule |
|-------|------|------|
| `job_id` | string (UUID) | **must** identify the created job |
| `upload_url` | string (URL) | **must** be a presigned PUT URL for the input object; clients **must** PUT image bytes here, not to the API |
| `input_key` | string | **must** be the S3 object key the upload targets (layout defined in the S3 key spec) |
| `status` | string | **must** be `pending` |

### Error cases (bad input)

| Condition | Status | `error.code` |
|-----------|--------|--------------|
| `Content-Type` missing or not `application/json` | `415` | `unsupported_media_type` |
| Body is not valid JSON | `400` | `invalid_json` |
| Body is valid JSON but not an object | `400` | `invalid_request` |
| `content_type` missing | `400` | `invalid_request` |
| `content_type` present but not an allowed value | `400` | `unsupported_content_type` |
| Unknown field present | `400` | `invalid_request` |

Servers **should** return `5xx` with `error.code` `internal_error` for unexpected failures after validation succeeds.

### Example

```bash
curl -sS -X POST "$API_BASE/jobs" \
  -H 'Content-Type: application/json' \
  -d '{"content_type":"image/jpeg"}'
```

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "upload_url": "http://localhost:4566/thumbnail-input/uploads/a1b2c3d4-e5f6-7890-abcd-ef1234567890/original?X-Amz-Algorithm=AWS4-HMAC-SHA256&...",
  "input_key": "uploads/a1b2c3d4-e5f6-7890-abcd-ef1234567890/original",
  "status": "pending"
}
```

## Upload (not an API route)

After `POST /jobs`, the client **must** upload bytes with HTTP `PUT` to `upload_url`. This request targets S3 (or LocalStack S3), not the jobs API.

```bash
curl -sS -X PUT "$UPLOAD_URL" \
  -H 'Content-Type: image/jpeg' \
  --data-binary @./photo.jpg
```

The `Content-Type` on the PUT **must** match the `content_type` supplied to `POST /jobs` (and any signed headers required by the presigned URL). Exact key layout and CORS notes are defined in the S3 key spec.

## `GET /jobs/{job_id}`

Returns the current job document so clients can poll until a terminal overall status.

### Request

| Rule | Requirement |
|------|-------------|
| Method / path | **must** be `GET /jobs/{job_id}` |
| `job_id` path param | **must** be present |

No request body.

### Success response

| Rule | Requirement |
|------|-------------|
| Status | **must** be `200` |
| `Content-Type` | **must** be `application/json` |

| Field | Type | Rule |
|-------|------|------|
| `job_id` | string (UUID) | **must** equal the path parameter |
| `status` | string | **must** be one of `pending`, `processing`, `complete`, `failed` |
| `input_key` | string | **must** be the input object key for the job |
| `sizes` | object | **must** map each configured size label (decimal string, e.g. `"128"`) to a size status object |
| `created_at` | string (timestamp) | **must** be the job creation time |
| `updated_at` | string (timestamp) | **must** be the last update time |

#### Size status object

| Field | Type | Rule |
|-------|------|------|
| `status` | string | **must** be one of `pending`, `processing`, `complete`, `failed` |
| `output_key` | string or `null` | **must** be the output object key when that size is `complete`; **must** be `null` until then |

### Not found and bad path

| Condition | Status | `error.code` |
|-----------|--------|--------------|
| `job_id` is not a valid UUID | `400` | `invalid_job_id` |
| No job exists for a well-formed `job_id` | `404` | `not_found` |

### Example (processing)

```bash
curl -sS "$API_BASE/jobs/$JOB_ID"
```

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "processing",
  "input_key": "uploads/a1b2c3d4-e5f6-7890-abcd-ef1234567890/original",
  "sizes": {
    "128": { "status": "complete", "output_key": "thumbnails/a1b2c3d4-e5f6-7890-abcd-ef1234567890/128.jpg" },
    "256": { "status": "processing", "output_key": null },
    "512": { "status": "pending", "output_key": null }
  },
  "created_at": "2026-07-18T22:00:00Z",
  "updated_at": "2026-07-18T22:00:05Z"
}
```

### Example (complete)

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "complete",
  "input_key": "uploads/a1b2c3d4-e5f6-7890-abcd-ef1234567890/original",
  "sizes": {
    "128": { "status": "complete", "output_key": "thumbnails/a1b2c3d4-e5f6-7890-abcd-ef1234567890/128.jpg" },
    "256": { "status": "complete", "output_key": "thumbnails/a1b2c3d4-e5f6-7890-abcd-ef1234567890/256.jpg" },
    "512": { "status": "complete", "output_key": "thumbnails/a1b2c3d4-e5f6-7890-abcd-ef1234567890/512.jpg" }
  },
  "created_at": "2026-07-18T22:00:00Z",
  "updated_at": "2026-07-18T22:00:12Z"
}
```

## Create → upload → poll (happy path)

Matches the customer flow in the architecture plan: create a job, PUT the image to the presigned URL, poll until terminal status.

```bash
export API_BASE="http://localhost:4566/_aws/execute-api/<apiId>/dev"

RESP=$(curl -sS -X POST "$API_BASE/jobs" \
  -H 'Content-Type: application/json' \
  -d '{"content_type":"image/jpeg"}')
JOB_ID=$(echo "$RESP" | jq -r .job_id)
UPLOAD_URL=$(echo "$RESP" | jq -r .upload_url)

curl -sS -X PUT "$UPLOAD_URL" \
  -H 'Content-Type: image/jpeg' \
  --data-binary @./photo.jpg

while true; do
  STATUS=$(curl -sS "$API_BASE/jobs/$JOB_ID")
  echo "$STATUS" | jq -c '{status, sizes}'
  echo "$STATUS" | jq -e '.status == "complete" or .status == "failed"' >/dev/null && break
  sleep 1
done
```

## OpenAPI artifact

| Artifact | Path | Rule |
|----------|------|------|
| OpenAPI 3 document | [`openapi.yaml`](openapi.yaml) | **must** describe the same paths, fields, and status codes as this document |
| Swagger UI entrypoint | [`swagger.html`](swagger.html) | local browser view of `openapi.yaml` |

Regenerate is not required: both files are checked in and **must** be updated together when the contract changes.

### View locally (no LocalStack)

From the repo root:

```bash
just swagger
```

This serves `docs/specification/` over HTTP and prints a URL. Open that URL in a browser to browse the API via Swagger UI.

Equivalent without `just`:

```bash
cd docs/specification && python3 -m http.server 8090
# open http://127.0.0.1:8090/swagger.html
```

Do not open `swagger.html` as a `file://` URL; the UI loads `openapi.yaml` over HTTP.
