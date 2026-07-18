# Local deploy (LocalStack)

Local AWS edge for v1. Terraform and boto3 must use this endpoint — not real AWS.

## Prerequisites

- Docker with Compose v2 (`docker compose version`)
- Terraform `>= 1.5` (`terraform version`)

## Endpoint

| Item | Value |
|------|--------|
| Edge URL | `http://localhost:4566` |
| Host bind | `127.0.0.1:4566` |
| Compose service | `localstack` |
| Container name | `localstack-main` (override with `LOCALSTACK_DOCKER_NAME`) |

Point SDKs / providers at `http://localhost:4566` (e.g. `endpoint_url` / `AWS_ENDPOINT_URL`).

## Required services

Compose sets `SERVICES` to:

`s3`, `lambda`, `sqs`, `dynamodb`, `apigateway`, `iam`

Docker socket is mounted so Lambda can run containers.

## Start / stop

From repo root:

```bash
docker compose up -d
```

Wait until healthy:

```bash
docker compose ps
curl -sf http://localhost:4566/_localstack/health
```

Stop (containers removed; state in `./.localstack` is not required to persist across restarts):

```bash
docker compose down
```

## Terraform (`infra/`)

Working directory: `infra/`.

Provider is wired to LocalStack endpoints in `infra/providers.tf` (not real AWS). Apply with plain `terraform` from that directory — `tflocal` is optional and not required.

### Constraint: path-style S3

`s3_use_path_style = true` must stay enabled on the AWS provider. LocalStack + path-style is required so S3 presigned URLs resolve correctly. Do not switch to virtual-hosted-only S3 without updating clients and this note.

### Init / apply

With LocalStack healthy:

```bash
cd infra
terraform init
terraform apply -auto-approve
```

Empty/near-empty root module is expected until resource tickets add S3/SQS/DynamoDB/Lambda.

State is local (`infra/terraform.tfstate`, gitignored).

## Files

| Path | Role |
|------|------|
| `docker-compose.yml` | LocalStack service definition |
| `./.localstack/` | LocalStack volume dir (gitignored; not a persistence guarantee) |
| `infra/` | Terraform root (LocalStack AWS provider + placeholders) |
| `infra/providers.tf` | LocalStack endpoints + `s3_use_path_style` |
