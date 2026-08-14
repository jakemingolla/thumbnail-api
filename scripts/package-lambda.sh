#!/usr/bin/env bash
# Build Lambda deployment zips for LocalStack (API + pipeline).
# Incremental: reinstall deps only when uv.lock / platform / Python version
# change; skip zip rewrite when the payload hash is unchanged.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

OUT_DIR="${REPO_ROOT}/dist/lambda"
DEPS_DIR="${OUT_DIR}/.deps"
STAGE_DIR="${OUT_DIR}/.stage"
API_ZIP="${OUT_DIR}/api.zip"
PIPELINE_ZIP="${OUT_DIR}/pipeline.zip"
REQ_FILE="${OUT_DIR}/requirements.lambda.txt"
STAMP_FILE="${OUT_DIR}/.deps.stamp"
HASH_FILE="${OUT_DIR}/.payload.sha256"

# Lambda runs Linux in Docker via LocalStack. Default to host arch (Apple Silicon → aarch64).
# Use manylinux_2_28 so native deps (Pillow) resolve to published wheels instead of sdists
# (Pillow 12.x has no manylinux2014 / unknown-linux-gnu wheels for cp313).
# Override: LAMBDA_PYTHON_PLATFORM=x86_64-manylinux_2_28
default_platform() {
  case "$(uname -m)" in
    arm64 | aarch64) echo "aarch64-manylinux_2_28" ;;
    *) echo "x86_64-manylinux_2_28" ;;
  esac
}

PYTHON_PLATFORM="${LAMBDA_PYTHON_PLATFORM:-$(default_platform)}"
PYTHON_VERSION="${LAMBDA_PYTHON_VERSION:-3.13}"

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is required (run: just install)" >&2
  exit 1
fi

if ! command -v zip >/dev/null 2>&1; then
  echo "error: zip is required" >&2
  exit 1
fi

echo "Packaging Lambda artifacts (platform=${PYTHON_PLATFORM}, python=${PYTHON_VERSION})"

mkdir -p "${OUT_DIR}"
# Drop prior staging leftovers / failed atomic writes.
rm -rf "${STAGE_DIR}"
rm -f "${OUT_DIR}"/.tmp.*

lock_hash() {
  python3 - "${REPO_ROOT}/uv.lock" <<'PY'
import hashlib
import pathlib
import sys

print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
}

DEPS_STAMP="${PYTHON_PLATFORM}|${PYTHON_VERSION}|$(lock_hash)"
NEED_DEPS=0
if [[ ! -d "${DEPS_DIR}" || ! -d "${DEPS_DIR}/PIL" ]]; then
  NEED_DEPS=1
elif [[ ! -f "${STAMP_FILE}" ]] || [[ "$(cat "${STAMP_FILE}")" != "${DEPS_STAMP}" ]]; then
  NEED_DEPS=1
fi

if [[ "${NEED_DEPS}" -eq 1 ]]; then
  echo "Installing Lambda deps (uv.lock / platform / Python version changed)"
  rm -rf "${DEPS_DIR}"
  mkdir -p "${DEPS_DIR}"

  # Runtime deps only. Prune boto3 — provided by the Lambda Python runtime / LocalStack.
  # Native wheels (e.g. Pillow) resolve for PYTHON_PLATFORM.
  uv export \
    --frozen \
    --no-dev \
    --no-emit-project \
    --no-hashes \
    --prune boto3 \
    --output-file "${REQ_FILE}" \
    >/dev/null

  # Require wheels — do not compile native deps on the host (CI lacks jpeg headers).
  # Bytecode compile is on (no --no-compile-bytecode) so warm imports skip that work.
  uv pip install \
    --no-installer-metadata \
    --only-binary :all: \
    --python-version "${PYTHON_VERSION}" \
    --python-platform "${PYTHON_PLATFORM}" \
    --target "${DEPS_DIR}" \
    -r "${REQ_FILE}"

  printf '%s\n' "${DEPS_STAMP}" >"${STAMP_FILE}"
else
  echo "Reusing Lambda deps (${DEPS_DIR})"
fi

# Worker (THUMB-022) needs Pillow at runtime — fail fast if the Linux wheel missing.
if [[ ! -d "${DEPS_DIR}/PIL" ]]; then
  echo "error: Pillow (PIL) missing from Lambda deps under ${DEPS_DIR}" >&2
  echo "  platform=${PYTHON_PLATFORM} python=${PYTHON_VERSION}" >&2
  echo "  check dist/lambda/requirements.lambda.txt and manylinux wheel resolution" >&2
  exit 1
fi

payload_hash() {
  python3 - "${DEPS_DIR}" "${REPO_ROOT}/src/thumbnail_api" <<'PY'
import hashlib
import sys
from pathlib import Path

h = hashlib.sha256()


def add_tree(root: Path, *, skip_pycache: bool) -> None:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if skip_pycache and ("__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}):
            continue
        files.append(path)
    for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")


add_tree(Path(sys.argv[1]), skip_pycache=False)
add_tree(Path(sys.argv[2]), skip_pycache=True)
print(h.hexdigest())
PY
}

PAYLOAD_HASH="$(payload_hash)"
if [[ -f "${API_ZIP}" && -f "${PIPELINE_ZIP}" && -f "${HASH_FILE}" ]] \
  && [[ "$(cat "${HASH_FILE}")" == "${PAYLOAD_HASH}" ]]; then
  bytes() {
    local f="$1"
    if stat -f%z "${f}" >/dev/null 2>&1; then
      stat -f%z "${f}"
    else
      stat -c%s "${f}"
    fi
  }
  echo "Lambda zips unchanged (payload ${PAYLOAD_HASH:0:12}…); skipped rewrite"
  echo "Wrote:"
  echo "  ${API_ZIP} ($(bytes "${API_ZIP}") bytes)"
  echo "  ${PIPELINE_ZIP} ($(bytes "${PIPELINE_ZIP}") bytes)"
  echo "  ${REQ_FILE}"
  echo "Terraform: filename = \"\${path.module}/../dist/lambda/api.zip\" (or pipeline.zip)"
  exit 0
fi

# Assemble payload: cached deps + current application package.
mkdir -p "${STAGE_DIR}"
tar -C "${DEPS_DIR}" -cf - . | tar -C "${STAGE_DIR}" -xf -
rm -rf "${STAGE_DIR}/thumbnail_api"
tar -C "${REPO_ROOT}/src" \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  -cf - thumbnail_api \
  | tar -C "${STAGE_DIR}" -xf -

write_zip() {
  local dest="$1"
  local tmp
  # mktemp creates an empty file; zip treats that as a corrupt archive — remove first.
  tmp="$(mktemp "${OUT_DIR}/.tmp.XXXXXX")"
  rm -f "${tmp}"
  (
    cd "${STAGE_DIR}"
    # Include dep bytecode; exclude installer RECORD noise.
    zip -qr "${tmp}" . \
      -x '*.dist-info/RECORD'
  )
  mv -f "${tmp}" "${dest}"
}

write_zip "${API_ZIP}"
# Same payload (includes Pillow for the worker). Distinct filenames for Terraform wiring.
cp -f "${API_ZIP}" "${PIPELINE_ZIP}"
printf '%s\n' "${PAYLOAD_HASH}" >"${HASH_FILE}"

rm -rf "${STAGE_DIR}"

bytes() {
  local f="$1"
  if stat -f%z "${f}" >/dev/null 2>&1; then
    stat -f%z "${f}"
  else
    stat -c%s "${f}"
  fi
}

echo "Wrote:"
echo "  ${API_ZIP} ($(bytes "${API_ZIP}") bytes)"
echo "  ${PIPELINE_ZIP} ($(bytes "${PIPELINE_ZIP}") bytes)"
echo "  ${REQ_FILE}"
echo "Terraform: filename = \"\${path.module}/../dist/lambda/api.zip\" (or pipeline.zip)"
