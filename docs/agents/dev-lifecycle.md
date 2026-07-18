# Dev lifecycle (LocalStack workspace)

How agents start, isolate, and tear down local Docker/LocalStack for this repo. Deploy/apply steps: [`local-deploy.md`](local-deploy.md). PR gate: [`pull-requests.md`](pull-requests.md).

## Parallel agents (isolation)

Multiple agents on one machine **must not** share a LocalStack instance. Prefer one git worktree per agent.

`just localstack-up` auto-allocates conflict-free values and writes them to gitignored `.localstack.env`:

| Variable | Purpose |
|----------|---------|
| `COMPOSE_PROJECT_NAME` | Compose project (`thumbnail-<id>`) |
| `LOCALSTACK_DOCKER_NAME` | Container name (`localstack-<id>`) |
| `LOCALSTACK_EDGE_PORT` | Free host edge port |
| `LOCALSTACK_EXTERNAL_HOST_START` / `_END` | Free host remap of container ports `4510-4559` |
| `LOCALSTACK_VOLUME_DIR` | Per-instance data dir (`.localstack-<id>/`) |
| `LOCALSTACK_ENDPOINT` | `http://127.0.0.1:<edge-port>` for SDKs / Terraform |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Dummy `test` / `test` for aws CLI / SDKs (matches Terraform defaults) |
| `AWS_DEFAULT_REGION` / `AWS_REGION` | `us-east-1` |
| `AWS_ENDPOINT_URL` | Same as `LOCALSTACK_ENDPOINT` |

Do not run bare `docker compose up` for agent work — it defaults to shared name/ports and will collide.

## Start

From repo root:

```bash
just localstack-up
```

Wait is built into the script (health check against `LOCALSTACK_ENDPOINT`). Inspect:

```bash
set -a && source .localstack.env && set +a
docker compose --env-file .localstack.env ps
curl -sf "$LOCALSTACK_ENDPOINT/_localstack/health"
```

## Stop / cleanup

Full teardown for **this worktree** (containers, instance volume, `.localstack.env`, local Terraform state):

```bash
just localstack-down
```

`localstack-down` is idempotent. It does not stop other worktrees’ instances.

## PR cleanup gate

Before opening a PR, cleanup is mandatory:

```bash
just localstack-down
just localstack-assert-clean
```

Do not run `gh pr create` if `just localstack-assert-clean` fails. Details: [`pull-requests.md`](pull-requests.md).

`localstack-assert-clean` only checks this worktree (env file, volume dirs, local Terraform state).

## Files

| Path | Role |
|------|------|
| `scripts/localstack-up.sh` | Allocate unique instance + start |
| `scripts/localstack-down.sh` | Full teardown for this worktree |
| `scripts/localstack-assert-clean.sh` | PR gate: fail if leftovers remain |
| `scripts/lib/localstack.sh` | Shared port/env helpers |
| `.localstack.env` | Generated instance env (gitignored) |
| `.localstack-<id>/` | Per-instance volume dir (gitignored) |
