# Local runbook

Run the thumbnail API on your machine with LocalStack, then exercise the create → upload → poll happy path with curl.

For agent-oriented command checklists and verify steps, see [`docs/agents/local-deploy.md`](../agents/local-deploy.md). The HTTP contract lives in [`docs/specification/api.md`](../specification/api.md).

## Prerequisites

- **Python 3.13.5** (match `.python-version`)
- **[just](https://github.com/casey/just)** — `brew install just` or see upstream install docs
- **Docker** with Compose v2 (`docker compose version`)
- **Terraform** `>= 1.5` (`terraform version`)
- **jq** — for the curl examples below
- A sample image on disk (e.g. `./photo.jpg`) for the upload step

`just install` installs the pinned **uv** if needed and syncs dependencies. You do not need real AWS credentials; LocalStack uses dummy `test` / `test` keys written into `.localstack.env`.

## Bring the stack up

From the repo root, after clone:

```bash
just install
just deploy
```

That one-shot recipe starts LocalStack (Compose), builds Lambda zips, applies Terraform against the local edge, and prints outputs. Equivalent step-by-step:

```bash
just localstack-up   # allocate ports/names for this checkout, start, wait healthy
just package         # Lambda zips → dist/lambda/
just apply           # terraform init + apply against LocalStack
just outputs         # print API_BASE and other key outputs
```

Each checkout gets its own LocalStack ports and names (safe if you have multiple worktrees). After `localstack-up`, load the generated env when you need the edge URL or AWS CLI against LocalStack:

```bash
set -a && source .localstack.env && set +a
curl -sf "$LOCALSTACK_ENDPOINT/_localstack/health"
```

Teardown when finished: `just localstack-down`. Before opening a PR, also run `just localstack-assert-clean`.

## Obtain `API_BASE`

After a successful apply:

```bash
just outputs
```

Copy or eval the printed `export API_BASE=...` line. Or:

```bash
export API_BASE="$(cd infra && terraform output -raw api_base_url)"
```

LocalStack bases look like:

`http://127.0.0.1:<edge-port>/_aws/execute-api/<apiId>/dev`

All paths below are relative to that base (stage included).

## Happy path: create → upload → poll

Matches the v1 customer flow in the [API spec](../specification/api.md): create a job, PUT the image to the presigned URL (not through the API), poll until a terminal status.

```bash
export API_BASE="$(cd infra && terraform output -raw api_base_url)"
set -a && source .localstack.env && set +a

RESP=$(curl -sS -X POST "$API_BASE/jobs" \
  -H 'Content-Type: application/json' \
  -d '{"content_type":"image/jpeg"}')
echo "$RESP" | jq .
JOB_ID=$(echo "$RESP" | jq -r .job_id)
UPLOAD_URL=$(echo "$RESP" | jq -r .upload_url)

# Presigned hosts from Lambda use the in-container edge; rewrite host for host-side curl.
# Keep path-style (/bucket/key?...). Do not switch to virtual-hosted (bucket.localhost...).
UPLOAD_URL=$(python3 -c "
from urllib.parse import urlparse, urlunparse
import os, sys
u, e = urlparse(sys.argv[1]), urlparse(os.environ['LOCALSTACK_ENDPOINT'])
print(urlunparse(u._replace(scheme=e.scheme or 'http', netloc=e.netloc)))
" "$UPLOAD_URL")

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

`POST /jobs` returns `201` with `job_id`, `upload_url`, `input_key`, and `status: pending`. `GET /jobs/{job_id}` returns the job document; overall `status` moves through `pending` → `processing` → `complete` (or `failed`). Per-size entries under `sizes` carry their own status and `output_key` when complete.

Allowed `content_type` values: `image/jpeg`, `image/png`, `image/webp`. The PUT `Content-Type` must match what you sent on create.

Browse the contract in a browser (no LocalStack required): `just swagger`.

## LocalStack S3 / presign note

LocalStack needs **path-style** S3 addressing so presigned upload URLs resolve correctly (`http://host:port/bucket/key?...`). Use the returned URL shape as-is — do not rewrite to virtual-hosted style (`http://bucket.host/...`).

From your laptop, you usually only need to swap the **host/port** of `upload_url` to `LOCALSTACK_ENDPOINT` (as in the script above). Lambdas sign against the in-container edge (`localhost.localstack.cloud`); host curl must hit the mapped edge port.

## What v1 does not include

Do not expect these in the local stack or the public API:

- Auth, API keys, Cognito, or authorizers
- Real AWS (this runbook is LocalStack-only)
- Push / webhook notifications when a job finishes — clients poll `GET /jobs/{job_id}`
- Listing jobs, rate limits, pagination, or presigned download URLs for completed thumbnails

Image bytes never go through API Gateway; only the JSON jobs API and the S3 presigned PUT are in play.
