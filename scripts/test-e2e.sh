#!/usr/bin/env bash
# LocalStack e2e harness: Compose up → package → terraform apply → pytest test/e2e/.
# Feature PRs add scenarios under test/e2e/; do not invent a second harness.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/localstack.sh"

cd "${REPO_ROOT}"

STARTED_BY_HARNESS=0

in_ci() {
  [[ "${CI:-}" == "true" || "${GITHUB_ACTIONS:-}" == "true" ]]
}

cleanup() {
  local ec=$?
  if [[ "${STARTED_BY_HARNESS}" -eq 1 ]] || in_ci; then
    echo "e2e: tearing down LocalStack (endpoint=${LOCALSTACK_ENDPOINT:-unknown}) ..."
    "${SCRIPT_DIR}/localstack-down.sh" || true
  else
    echo "e2e: leaving existing LocalStack running (endpoint=${LOCALSTACK_ENDPOINT:-unknown})"
  fi
  exit "${ec}"
}
trap cleanup EXIT

already_up=0
if load_localstack_env; then
  if curl -sf "${LOCALSTACK_ENDPOINT}/_localstack/health" >/dev/null 2>&1; then
    already_up=1
  fi
fi

if [[ "${already_up}" -eq 1 ]]; then
  echo "e2e: reusing healthy LocalStack at ${LOCALSTACK_ENDPOINT}"
else
  echo "e2e: starting LocalStack ..."
  "${SCRIPT_DIR}/localstack-up.sh"
  STARTED_BY_HARNESS=1
fi

load_localstack_env
echo "e2e: LocalStack endpoint=${LOCALSTACK_ENDPOINT}"
echo "e2e: credentials AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID:-unset} region=${AWS_REGION:-unset}"

# Package zips as available (idempotent; required once Terraform creates Lambdas).
echo "e2e: packaging Lambda artifacts ..."
just package

# Terraform defaults lambda_architectures to arm64 (Apple Silicon). CI/ubuntu and
# other x86_64 hosts must deploy x86_64 so LocalStack can invoke the zip natively
# (arm64 functions hang under qemu / fail to start on amd64 runners).
lambda_arch_for_host() {
  case "$(uname -m)" in
    arm64 | aarch64) echo "arm64" ;;
    *) echo "x86_64" ;;
  esac
}
LAMBDA_ARCH="$(lambda_arch_for_host)"
echo "e2e: terraform lambda_architectures=[\"${LAMBDA_ARCH}\"] (host=$(uname -m))"

echo "e2e: terraform init + apply (endpoint=${LOCALSTACK_ENDPOINT}) ..."
(
  cd "${REPO_ROOT}/infra"
  terraform init -input=false
  if ! terraform apply -auto-approve -input=false \
    -var="localstack_endpoint=${LOCALSTACK_ENDPOINT}" \
    -var="lambda_architectures=[\"${LAMBDA_ARCH}\"]"; then
    echo "error: terraform apply failed" >&2
    echo "  LocalStack endpoint: ${LOCALSTACK_ENDPOINT}" >&2
    echo "  lambda_architectures: [\"${LAMBDA_ARCH}\"]" >&2
    echo "  Hint: curl -sf \"${LOCALSTACK_ENDPOINT}/_localstack/health\" | jq ." >&2
    exit 1
  fi
)

echo "e2e: running pytest test/e2e/ ..."
uv run python -m pytest test/e2e/ -m e2e "$@"
