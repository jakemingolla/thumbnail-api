#!/usr/bin/env bash
# Stop and fully remove this worktree's LocalStack instance (containers, volumes, env).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/localstack.sh"

cd "${REPO_ROOT}"

if [[ -f "${LOCALSTACK_ENV_FILE}" ]]; then
  load_localstack_env

  if [[ -n "${COMPOSE_PROJECT_NAME:-}" ]]; then
    echo "Stopping compose project ${COMPOSE_PROJECT_NAME} ..."
    compose down -v --remove-orphans >/dev/null 2>&1 || true

    # Remove any leftover containers labeled for this compose project (e.g. Lambda).
    ids="$(docker ps -aq --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" 2>/dev/null || true)"
    if [[ -n "${ids}" ]]; then
      # shellcheck disable=SC2086
      docker rm -f ${ids} >/dev/null 2>&1 || true
    fi
  fi

  if [[ -n "${LOCALSTACK_DOCKER_NAME:-}" ]]; then
    docker rm -f "${LOCALSTACK_DOCKER_NAME}" >/dev/null 2>&1 || true
  fi

  if [[ -n "${LOCALSTACK_VOLUME_DIR:-}" ]]; then
    remove_path_if_exists "${LOCALSTACK_VOLUME_DIR}"
  fi

  rm -f "${LOCALSTACK_ENV_FILE}"
else
  echo "No ${LOCALSTACK_ENV_FILE}; nothing allocated for this worktree."
fi

# Default / legacy paths from before instance isolation.
remove_path_if_exists "${REPO_ROOT}/.localstack"

# Orphan instance volume dirs left if env file was deleted manually.
shopt -s nullglob
for dir in "${REPO_ROOT}"/.localstack-*; do
  remove_path_if_exists "${dir}"
done
shopt -u nullglob

# Local Terraform state is tied to the wiped LocalStack; drop it so the next
# apply does not refresh against a dead endpoint.
remove_path_if_exists "${REPO_ROOT}/infra/terraform.tfstate"
remove_path_if_exists "${REPO_ROOT}/infra/terraform.tfstate.backup"
shopt -s nullglob
for f in "${REPO_ROOT}"/infra/terraform.tfstate.*; do
  remove_path_if_exists "${f}"
done
shopt -u nullglob

echo "LocalStack cleanup complete for this worktree."
