# Local deploy (LocalStack)

Local AWS edge for v1. Terraform and boto3 must use this endpoint — not real AWS.

Start/stop, isolation, and cleanup: [`dev-lifecycle.md`](dev-lifecycle.md).

## Prerequisites

- Docker with Compose v2 (`docker compose version`)
- Terraform `>= 1.5` (`terraform version`)
- A running LocalStack instance for this worktree (`just localstack-up` — see [`dev-lifecycle.md`](dev-lifecycle.md))

## Endpoint

| Item | Value |
|------|--------|
| Edge URL | `LOCALSTACK_ENDPOINT` from `.localstack.env` |
| Host bind | `127.0.0.1:<LOCALSTACK_EDGE_PORT>` |
| Compose service | `localstack` |
| Container name | `LOCALSTACK_DOCKER_NAME` from `.localstack.env` |

Point SDKs / providers at `LOCALSTACK_ENDPOINT` (e.g. `endpoint_url` / `AWS_ENDPOINT_URL`).

```bash
set -a && source .localstack.env && set +a
curl -sf "$LOCALSTACK_ENDPOINT/_localstack/health"
```

Sourcing `.localstack.env` also sets dummy `AWS_*` credentials (`test` / `test`), region, and `AWS_ENDPOINT_URL` so `aws` / SDKs work against LocalStack without `aws login`.

## Required services

Compose sets `SERVICES` to:

`s3`, `lambda`, `sqs`, `dynamodb`, `apigateway`, `iam`

Docker socket is mounted so Lambda can run containers.

## Terraform (`infra/`)

Working directory: `infra/`.

Provider is wired to LocalStack endpoints in `infra/providers.tf` (not real AWS). Apply with plain `terraform` from that directory — `tflocal` is optional and not required.

Pass the allocated endpoint (after `just localstack-up`):

```bash
cd infra
terraform init
set -a && source ../.localstack.env && set +a
terraform apply -auto-approve -var="localstack_endpoint=${LOCALSTACK_ENDPOINT}"
```

State is local (`infra/terraform.tfstate`, gitignored). Teardown deletes that state with the LocalStack instance — see [`dev-lifecycle.md`](dev-lifecycle.md).

### Constraint: path-style S3

`s3_use_path_style = true` must stay enabled on the AWS provider. LocalStack + path-style is required so S3 presigned URLs resolve correctly. Do not switch to virtual-hosted-only S3 without updating clients and this note.

Application boto3 S3 clients must also use path-style addressing (`addressing_style=path`), SigV4 (`signature_version=s3v4`), and `endpoint_url` from `AWS_ENDPOINT_URL` — see `src/thumbnail_api/config/clients.py`.

Presigned upload URLs from `thumbnail_api.s3.generate_presigned_put_url` are path-style against that endpoint (e.g. `http://127.0.0.1:<port>/<bucket>/<key>?...`). Clients (`curl`, browsers) must use the returned URL as-is — do not rewrite to virtual-hosted style (`http://<bucket>.localhost:...`).

### Verify S3 buckets

Default names (from `name_prefix`, default `thumbnail`): `thumbnail-input`, `thumbnail-output`. Overrides: `input_bucket_name` / `output_bucket_name`.

```bash
set -a && source .localstack.env && set +a
aws --endpoint-url "$LOCALSTACK_ENDPOINT" s3 ls
# expect: thumbnail-input and thumbnail-output
```

(`awslocal s3 ls` is equivalent if installed.) Terraform outputs `input_bucket_name` / `output_bucket_name` after apply.

### Verify DynamoDB jobs table

Default name (from `name_prefix`, default `thumbnail`): `thumbnail-jobs`. Override: `jobs_table_name`. Terraform output: `jobs_table_name` (for later `JOBS_TABLE` Lambda env).

```bash
set -a && source .localstack.env && set +a
TABLE=$(cd infra && terraform output -raw jobs_table_name)
aws --endpoint-url "$LOCALSTACK_ENDPOINT" dynamodb describe-table --table-name "$TABLE"
# expect: TableName thumbnail-jobs, KeySchema HASH AttributeName job_id, BillingMode PAY_PER_REQUEST
```

(`awslocal dynamodb describe-table --table-name "$TABLE"` is equivalent if installed.)

### SQS queues (after apply)

Terraform creates `{name_prefix}-work` and `{name_prefix}-work-dlq` (defaults `thumbnail-work`, `thumbnail-work-dlq`). Useful outputs: `work_queue_url`, `work_queue_arn`, `work_dlq_url`, `work_dlq_arn`, `sqs_max_receive_count`.

Verify against the running instance:

```bash
set -a && source .localstack.env && set +a
aws --endpoint-url="$LOCALSTACK_ENDPOINT" sqs list-queues
# or: terraform -chdir=infra output
```

(`awslocal` is equivalent if installed; plain `aws` + `--endpoint-url` is enough.)

### API Lambda IAM roles

API Lambdas use distinct roles from the pipeline (`thumbnail-api-create-job`, `thumbnail-api-get-job` by default): DynamoDB create/get on the jobs table and input-bucket `PutObject` for presigned uploads only — no SQS or output-bucket write. Outputs: `api_create_job_role_arn`, `api_get_job_role_arn`.

### Pipeline Lambda IAM roles

Dispatcher and worker use distinct roles from the API Lambdas (`thumbnail-dispatcher`, `thumbnail-worker` by default): SQS send vs consume, jobs table updates, and (worker only) input read / output write. Outputs: `dispatcher_role_arn`, `worker_role_arn`.

## Lambda packaging

Build deployable zip artifacts before Terraform creates Lambda functions (`filename`):

```bash
just package
```

Idempotent: re-running replaces zips under `dist/lambda/` (gitignored). No manual cleanup.

| Artifact | Path (from repo root) | Terraform use |
|----------|----------------------|---------------|
| API handlers (`create_job`, `get_job`) | `dist/lambda/api.zip` | `filename = "${path.module}/../dist/lambda/api.zip"` |
| Pipeline handlers (`dispatcher`, `worker`) | `dist/lambda/pipeline.zip` | `filename = "${path.module}/../dist/lambda/pipeline.zip"` |

Both zips currently share the same payload (installable `thumbnail_api` + runtime third-party deps). Handler entrypoints differ per function, e.g. `thumbnail_api.handlers.create_job.handler` / `thumbnail_api.handlers.get_job.handler` (wire in THUMB-016). Extend or split `pipeline.zip` when dispatcher/worker land (THUMB-019 / THUMB-022) if their deps diverge.

### Native deps (Pillow)

Zips target **Linux** wheels for LocalStack’s Docker Lambda runtime (not macOS host wheels):

| Host arch | Default `--python-platform` |
|-----------|------------------------------|
| `arm64` / `aarch64` | `aarch64-unknown-linux-gnu` |
| otherwise | `x86_64-unknown-linux-gnu` |

Override with `LAMBDA_PYTHON_PLATFORM` (and optionally `LAMBDA_PYTHON_VERSION`, default `3.13`) if your LocalStack Lambda arch differs.

- **boto3 / botocore**: omitted from the zip (pruned at export); use the runtime-provided SDK.
- **Pillow** (and other native wheels): not a project dependency yet; when added under `[project].dependencies`, `just package` installs manylinux wheels for the platform above — do not `pip install` Pillow on the Mac host into the artifact.
- Prefer zip + correct platform for LocalStack. Container/`image_uri` packaging is out of scope unless zip proves insufficient.

Exported requirements (debug): `dist/lambda/requirements.lambda.txt`.

## Lambda / app environment variables

Loaded by `thumbnail_api.config.get_config()` (`src/thumbnail_api/config/types.py`). Missing required values fail fast.

### Required

| Variable | Purpose |
|----------|---------|
| `ENVIRONMENT` | Runtime environment label |
| `INPUT_BUCKET` | S3 bucket for original uploads |
| `OUTPUT_BUCKET` | S3 bucket for thumbnail objects |
| `JOBS_TABLE` | DynamoDB jobs table name |
| `QUEUE_URL` | SQS work queue URL |
| `AWS_ENDPOINT_URL` | LocalStack edge URL for boto3 (`endpoint_url`) |

Set `AWS_ENDPOINT_URL` to `LOCALSTACK_ENDPOINT` from `.localstack.env` when running against this worktree’s LocalStack.

### Optional

| Variable | Default | Purpose |
|----------|---------|---------|
| `AWS_REGION` | `us-east-1` | Region passed to boto3 clients |
| `THUMBNAIL_SIZES` | `128,256,512` | Comma-separated thumbnail sizes (pixels); must match `docs/specification/sqs-messages.md` unless that contract is updated |

## Files

| Path | Role |
|------|------|
| `docker-compose.yml` | LocalStack service (env-driven name/ports/volume) |
| `infra/` | Terraform root (LocalStack AWS provider + resources) |
| `infra/providers.tf` | LocalStack endpoints + `s3_use_path_style` |
| `infra/s3.tf` | Input / output buckets + input-bucket CORS |
| `infra/dynamodb.tf` | Jobs table (`job_id` partition key, on-demand) |
| `infra/sqs.tf` | Work queue + DLQ + redrive |
| `infra/iam_api.tf` | IAM roles for create_job / get_job (not pipeline) |
| `infra/iam_pipeline.tf` | IAM roles for dispatcher / worker (not API) |
| `scripts/package-lambda.sh` | `just package` — build `dist/lambda/*.zip` |
| `dist/lambda/api.zip` | API Lambda zip (generated; gitignored) |
| `dist/lambda/pipeline.zip` | Pipeline Lambda zip (generated; gitignored) |
| `src/thumbnail_api/config/` | Shared env config + LocalStack-aware boto3 clients |
| `src/thumbnail_api/s3/` | Key builders, path-style presigned PUT, worker get/put |
| `.env.example` | Sample Lambda/app env var names for local runs |
| `.localstack.env` | Generated instance env (gitignored; created by lifecycle) |
