#!/usr/bin/env bash
# Dummy-invoke the four Lambdas so LocalStack pre-starts their containers.
# Handler errors are expected and ignored; missing endpoint / state is not.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/localstack.sh"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/prereqs.sh"

cd "${REPO_ROOT}"

require_terraform

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is required (run: just install)" >&2
  exit 1
fi

if ! load_localstack_env; then
  echo "error: ${LOCALSTACK_ENV_FILE} missing — run: just localstack-up" >&2
  exit 1
fi

if [[ -z "${LOCALSTACK_ENDPOINT:-}" ]]; then
  echo "error: LOCALSTACK_ENDPOINT unset in ${LOCALSTACK_ENV_FILE}" >&2
  echo "  Recreate the instance: just localstack-down && just localstack-up" >&2
  exit 1
fi

STATE_FILE="${REPO_ROOT}/infra/terraform.tfstate"
if [[ ! -f "${STATE_FILE}" ]]; then
  echo "error: ${STATE_FILE} missing — apply the stack first: just apply" >&2
  exit 1
fi

tf_raw() {
  local name="$1"
  (cd "${REPO_ROOT}/infra" && terraform output -raw "${name}")
}

CREATE_FN="$(tf_raw api_create_job_function_name)"
GET_FN="$(tf_raw api_get_job_function_name)"
DISPATCHER_FN="$(tf_raw dispatcher_function_name)"
WORKER_FN="$(tf_raw worker_function_name)"

echo "warmup: LocalStack endpoint=${LOCALSTACK_ENDPOINT}"
echo "warmup: invoking ${CREATE_FN}, ${GET_FN}, ${DISPATCHER_FN}, ${WORKER_FN}"

uv run python - "${LOCALSTACK_ENDPOINT}" \
  "${CREATE_FN}" "${GET_FN}" "${DISPATCHER_FN}" "${WORKER_FN}" <<'PY'
from __future__ import annotations

import os
import sys

import boto3

endpoint = sys.argv[1]
names = sys.argv[2:]
client = boto3.client(
    "lambda",
    endpoint_url=endpoint,
    region_name=os.environ.get("AWS_REGION")
    or os.environ.get("AWS_DEFAULT_REGION")
    or "us-east-1",
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
)
payload = b"{}"
for name in names:
    try:
        response = client.invoke(
            FunctionName=name,
            InvocationType="RequestResponse",
            Payload=payload,
        )
        status = response.get("StatusCode")
        func_err = response.get("FunctionError") or "-"
        print(f"warmup: {name} status={status} function_error={func_err}")
    except Exception as exc:  # noqa: BLE001 — dummy invoke; container start is the goal
        print(f"warmup: {name} invoke failed (ignored): {exc}")
print("warmup: done")
PY
