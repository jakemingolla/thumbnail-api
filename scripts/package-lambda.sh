#!/usr/bin/env bash
# Build Lambda deployment zips for LocalStack (API + pipeline).
# Idempotent: re-running replaces dist/lambda/*.zip without manual cleanup.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

OUT_DIR="${REPO_ROOT}/dist/lambda"
BUILD_DIR="${OUT_DIR}/.build"
API_ZIP="${OUT_DIR}/api.zip"
PIPELINE_ZIP="${OUT_DIR}/pipeline.zip"

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

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}" "${OUT_DIR}"
# Drop prior staging leftovers / failed atomic writes.
rm -f "${OUT_DIR}"/.tmp.*

REQ_FILE="${OUT_DIR}/requirements.lambda.txt"

# Runtime deps only. Prune boto3 — provided by the Lambda Python runtime / LocalStack.
# Native wheels (e.g. Pillow, when added to project deps) resolve for PYTHON_PLATFORM.
uv export \
  --frozen \
  --no-dev \
  --no-emit-project \
  --no-hashes \
  --prune boto3 \
  --output-file "${REQ_FILE}" \
  >/dev/null

# Require wheels — do not compile native deps on the host (CI lacks jpeg headers).
uv pip install \
  --no-installer-metadata \
  --no-compile-bytecode \
  --only-binary :all: \
  --python-version "${PYTHON_VERSION}" \
  --python-platform "${PYTHON_PLATFORM}" \
  --target "${BUILD_DIR}" \
  -r "${REQ_FILE}"

# Application package (handlers live under thumbnail_api.*; same zip for all functions).
rm -rf "${BUILD_DIR}/thumbnail_api"
# Copy without bytecode caches (avoid find -exec under tight ARG_MAX / sandboxes).
tar -C "${REPO_ROOT}/src" \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  -cf - thumbnail_api \
  | tar -C "${BUILD_DIR}" -xf -

write_zip() {
  local dest="$1"
  local tmp
  # mktemp creates an empty file; zip treats that as a corrupt archive — remove first.
  tmp="$(mktemp "${OUT_DIR}/.tmp.XXXXXX")"
  rm -f "${tmp}"
  (
    cd "${BUILD_DIR}"
    # Stable, reproducible-enough archive for local iteration (no junk paths).
    zip -qr "${tmp}" . \
      -x '*/__pycache__/*' \
      -x '*.pyc' \
      -x '*.pyo' \
      -x '*.dist-info/RECORD'
  )
  mv -f "${tmp}" "${dest}"
}

write_zip "${API_ZIP}"
# Same payload today; Terraform can pin distinct filenames. Split later if worker deps diverge.
cp -f "${API_ZIP}" "${PIPELINE_ZIP}"

# Drop the staging tree; keep requirements list for debugging / Terraform notes.
rm -rf "${BUILD_DIR}"

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
