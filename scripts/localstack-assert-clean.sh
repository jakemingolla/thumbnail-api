#!/usr/bin/env bash
# Exit 0 only if this worktree has no LocalStack leftovers.
# Agents must pass this check before opening a pull request.
# Does not fail on other worktrees' running instances (parallel agents OK).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/localstack.sh"

cd "${REPO_ROOT}"

errors=0

fail() {
  echo "error: $*" >&2
  errors=1
}

if [[ -f "${LOCALSTACK_ENV_FILE}" ]]; then
  fail "${LOCALSTACK_ENV_FILE} still exists (run: just localstack-down)"
fi

if [[ -d "${REPO_ROOT}/.localstack" ]]; then
  fail "${REPO_ROOT}/.localstack still exists (run: just localstack-down)"
fi

shopt -s nullglob
instance_dirs=("${REPO_ROOT}"/.localstack-*)
shopt -u nullglob
if ((${#instance_dirs[@]} > 0)); then
  fail "LocalStack volume dirs still present: ${instance_dirs[*]} (run: just localstack-down)"
fi

if [[ -f "${REPO_ROOT}/infra/terraform.tfstate" ]] || [[ -f "${REPO_ROOT}/infra/terraform.tfstate.backup" ]]; then
  fail "infra/terraform.tfstate* still present (run: just localstack-down)"
fi

shopt -s nullglob
state_extras=("${REPO_ROOT}"/infra/terraform.tfstate.*)
shopt -u nullglob
if ((${#state_extras[@]} > 0)); then
  fail "infra/terraform.tfstate.* still present (run: just localstack-down)"
fi

if [[ "${errors}" -ne 0 ]]; then
  echo >&2
  echo "LocalStack is not clean. Before opening a PR, run:" >&2
  echo "  just localstack-down" >&2
  echo "  just localstack-assert-clean" >&2
  exit 1
fi

echo "LocalStack clean: no worktree leftovers."
