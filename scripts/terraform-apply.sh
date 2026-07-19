#!/usr/bin/env bash
# terraform init + apply against this worktree's LocalStack instance.
# Requires: Docker (healthy LocalStack), terraform, just package artifacts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/localstack.sh"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/prereqs.sh"

cd "${REPO_ROOT}"

require_terraform
require_docker

if ! load_localstack_env; then
  echo "error: ${LOCALSTACK_ENV_FILE} missing — run: just localstack-up" >&2
  exit 1
fi

if [[ -z "${LOCALSTACK_ENDPOINT:-}" ]]; then
  echo "error: LOCALSTACK_ENDPOINT unset in ${LOCALSTACK_ENV_FILE}" >&2
  echo "  Recreate the instance: just localstack-down && just localstack-up" >&2
  exit 1
fi

if ! curl -sf "${LOCALSTACK_ENDPOINT}/_localstack/health" >/dev/null 2>&1; then
  echo "error: LocalStack is not healthy at ${LOCALSTACK_ENDPOINT}" >&2
  echo "  Start or recreate: just localstack-up" >&2
  echo "  Health: curl -sf \"${LOCALSTACK_ENDPOINT}/_localstack/health\" | jq ." >&2
  exit 1
fi

API_ZIP="${REPO_ROOT}/dist/lambda/api.zip"
PIPELINE_ZIP="${REPO_ROOT}/dist/lambda/pipeline.zip"
missing=0
for zip_path in "${API_ZIP}" "${PIPELINE_ZIP}"; do
  if [[ ! -f "${zip_path}" ]]; then
    echo "error: missing Lambda zip: ${zip_path}" >&2
    missing=1
  fi
done
if [[ "${missing}" -ne 0 ]]; then
  echo "  Build artifacts first: just package" >&2
  exit 1
fi

lambda_arch_for_host() {
  case "$(uname -m)" in
    arm64 | aarch64) echo "arm64" ;;
    *) echo "x86_64" ;;
  esac
}
LAMBDA_ARCH="$(lambda_arch_for_host)"

echo "apply: LocalStack endpoint=${LOCALSTACK_ENDPOINT}"
echo "apply: lambda_architectures=[\"${LAMBDA_ARCH}\"] (host=$(uname -m))"
echo "apply: terraform init + apply (infra/) ..."

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

API_BASE="$(cd "${REPO_ROOT}/infra" && terraform output -raw api_base_url)"
echo
echo "apply: success"
echo "  API_BASE=${API_BASE}"
echo "  Key outputs: just outputs"
echo "  Shell tip: set -a && source .localstack.env && set +a"
echo "             export API_BASE=\"\$(cd infra && terraform output -raw api_base_url)\""
