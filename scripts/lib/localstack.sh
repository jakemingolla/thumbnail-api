#!/usr/bin/env bash
# Shared helpers for LocalStack instance lifecycle (sourced by scripts).

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCALSTACK_ENV_FILE="${REPO_ROOT}/.localstack.env"
EXTERNAL_PORT_WIDTH=50

port_in_use() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  # Fallback: try binding via python (stdlib).
  python3 - "$port" <<'PY'
import socket, sys
port = int(sys.argv[1])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(("127.0.0.1", port))
except OSError:
    sys.exit(0)  # in use
finally:
    s.close()
sys.exit(1)  # free
PY
}

port_free() {
  ! port_in_use "$1"
}

range_free() {
  local start="$1"
  local width="$2"
  local p
  for ((p = start; p < start + width; p++)); do
    port_free "$p" || return 1
  done
  return 0
}

# Echo a free TCP port on 127.0.0.1, preferring candidates then scanning.
find_free_port() {
  local candidate
  for candidate in "$@"; do
    if port_free "$candidate"; then
      echo "$candidate"
      return 0
    fi
  done
  local p
  for ((p = 4700; p < 20000; p++)); do
    if port_free "$p"; then
      echo "$p"
      return 0
    fi
  done
  echo "error: no free TCP port found for LocalStack edge" >&2
  return 1
}

# Echo start of a free host port range of EXTERNAL_PORT_WIDTH consecutive ports.
find_free_port_range() {
  local start
  # Prefer non-overlapping blocks so parallel agents rarely collide.
  for start in $(seq 4510 100 30000); do
    if range_free "$start" "$EXTERNAL_PORT_WIDTH"; then
      echo "$start"
      return 0
    fi
  done
  echo "error: no free ${EXTERNAL_PORT_WIDTH}-port host range found for LocalStack" >&2
  return 1
}

load_localstack_env() {
  if [[ ! -f "${LOCALSTACK_ENV_FILE}" ]]; then
    return 1
  fi
  # shellcheck disable=SC1090
  set -a
  source "${LOCALSTACK_ENV_FILE}"
  set +a
  return 0
}

compose() {
  docker compose --env-file "${LOCALSTACK_ENV_FILE}" "$@"
}

remove_path_if_exists() {
  local path="$1"
  if [[ ! -e "${path}" ]]; then
    return 0
  fi
  if rm -rf "${path}" 2>/dev/null; then
    return 0
  fi
  # LocalStack may write root-owned files into the bind-mounted volume dir.
  if command -v docker >/dev/null 2>&1; then
    local parent base
    parent="$(cd "$(dirname "${path}")" && pwd)"
    base="$(basename "${path}")"
    docker run --rm -v "${parent}:/parent" alpine:3.20 \
      rm -rf "/parent/${base}" >/dev/null 2>&1 || true
  fi
  rm -rf "${path}" 2>/dev/null || true
}
