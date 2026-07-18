# Local deploy (LocalStack)

Local AWS edge for v1. Terraform and boto3 must use this endpoint — not real AWS.

## Prerequisites

- Docker with Compose v2 (`docker compose version`)

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

## Files

| Path | Role |
|------|------|
| `docker-compose.yml` | LocalStack service definition |
| `./.localstack/` | LocalStack volume dir (gitignored; not a persistence guarantee) |
