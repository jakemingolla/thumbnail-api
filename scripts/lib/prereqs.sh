#!/usr/bin/env bash
# Shared prerequisite checks for local deploy scripts (sourced, not executed).

require_command() {
  local name="$1"
  local hint="${2:-}"
  if ! command -v "${name}" >/dev/null 2>&1; then
    echo "error: ${name} is required but not found on PATH" >&2
    if [[ -n "${hint}" ]]; then
      echo "  ${hint}" >&2
    fi
    exit 1
  fi
}

require_docker() {
  require_command docker "Install Docker Desktop (or Engine) and ensure the daemon is running."
  if ! docker info >/dev/null 2>&1; then
    echo "error: cannot talk to Docker (is the daemon running?)" >&2
    exit 1
  fi
  if ! docker compose version >/dev/null 2>&1; then
    echo "error: Docker Compose v2 is required (\`docker compose version\`)" >&2
    echo "  Install Compose v2 or use a Docker distribution that includes it." >&2
    exit 1
  fi
}

require_terraform() {
  # Plain terraform + provider endpoints is the supported path (tflocal optional).
  require_command terraform \
    "Install Terraform >= 1.5 (https://developer.hashicorp.com/terraform/install). tflocal is not required."
}
