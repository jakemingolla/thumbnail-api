# S3 object key layout

Normative contract for object keys and bucket roles used by create-job, dispatcher, and worker. Implementers must address objects only via the patterns in this document.

Bucket **names** are configuration. Key **layout** is fixed.

## Bucket roles

| Role | Config variable (illustrative) | Purpose |
|------|--------------------------------|---------|
| Input | `INPUT_BUCKET` | Client uploads of the original image |
| Output | `OUTPUT_BUCKET` | Worker-written thumbnail objects |

- Input and output **may** be the same physical bucket or different buckets.
- Callers must resolve the correct role for each operation; they must not hard-code bucket names.
- Key prefixes below are relative to the bucket for that role (not namespaced by bucket name inside the key).

## Identifiers

### `job_id`

- Must be a non-empty string that uniquely identifies a job.
- Must not contain `/` or empty path segments.
- Create-job issues `job_id`; all subsequent key construction for that job must reuse the same value.

### `size`

- Must be a non-empty token naming one configured thumbnail size (for example `small`, `256`).
- Must not contain `/` or `.`.
- The set of valid `size` values is defined elsewhere; this document only requires that each size map to exactly one output key.

## Input keys

Pattern (normative):

```text
uploads/{job_id}/original
```

Rules:

- Create-job must mint a presigned upload URL for exactly this key in the **input** bucket.
- Clients must upload the original image to that key (typically via HTTP `PUT` to the presigned URL).
- The key has **no file extension**. Format is conveyed by `Content-Type`, not by the key.
- Exactly one original object per `job_id` at this key. Overwrite semantics for a second upload to the same key are undefined unless stated by a later ticket.

Example:

```text
uploads/550e8400-e29b-41d4-a716-446655440000/original
```

## Output keys

Pattern (normative), one object per size:

```text
thumbnails/{job_id}/{size}.jpg
```

Rules:

- Workers must write each thumbnail to this key in the **output** bucket.
- The extension must be `.jpg` (lowercase).
- Dispatcher, status APIs, and any consumer that locates a thumbnail must derive the key from `job_id` and `size` using this pattern — they must not invent alternate paths.

Examples:

```text
thumbnails/550e8400-e29b-41d4-a716-446655440000/small.jpg
thumbnails/550e8400-e29b-41d4-a716-446655440000/256.jpg
```

## Content-Type and extensions

### Uploads (input)

| Concern | Requirement |
|---------|-------------|
| Object key | `uploads/{job_id}/original` (no extension) |
| Allowed `Content-Type` | One of: `image/jpeg`, `image/png`, `image/webp` |
| Presigned `PUT` | Must require the client to send a `Content-Type` from the allowed set (signed header / condition) |
| Stored object | Must retain the `Content-Type` the client uploaded |

Clients should treat the original as opaque bytes typed by `Content-Type`. Workers must accept any allowed input type when reading the original.

### Thumbnails (output)

| Concern | Requirement |
|---------|-------------|
| Object key | `thumbnails/{job_id}/{size}.jpg` |
| `Content-Type` | Must be `image/jpeg` |
| Encoding | JPEG bytes matching the `.jpg` extension |

Workers must set `Content-Type: image/jpeg` when writing output objects.

## LocalStack / path-style addressing

Local development uses LocalStack (or equivalent) as the S3 endpoint.

- S3 clients and generated URLs **must** use **path-style** addressing against the LocalStack endpoint so browser and CLI clients resolve correctly, for example:

  ```text
  http://localhost:4566/{bucket}/{key}
  ```

- Virtual-hosted–style URLs (for example `http://{bucket}.localhost:4566/{key}`) must not be required for local clients; they often fail DNS/resolution for browsers talking to LocalStack.
- Presigned URLs returned to clients in local/dev must be usable as-is against that path-style endpoint (scheme, host, and port matching the LocalStack listener the client can reach).

Production AWS addressing may use the platform default (virtual-hosted–style); this path-style requirement applies to LocalStack-backed environments.

## CORS (non-normative note)

Browser-based uploads to presigned input URLs may require a CORS configuration on the input bucket. Fine-tuning CORS is out of scope for this document.

## Out of scope

- CloudFront distributions and public bucket policies
- CORS rule details beyond the note above
- Lifecycle, retention, and encryption settings
- The concrete set of thumbnail `size` values and pixel dimensions
