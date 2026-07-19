#!/usr/bin/env bash
# Print API_BASE and other key Terraform outputs for the local stack.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/localstack.sh"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/prereqs.sh"

cd "${REPO_ROOT}"

require_terraform

STATE_FILE="${REPO_ROOT}/infra/terraform.tfstate"
if [[ ! -f "${STATE_FILE}" ]]; then
  echo "error: ${STATE_FILE} missing — apply the stack first: just apply" >&2
  exit 1
fi

tf_raw() {
  local name="$1"
  (cd "${REPO_ROOT}/infra" && terraform output -raw "${name}")
}

API_BASE="$(tf_raw api_base_url)"

echo "API_BASE=${API_BASE}"
echo
echo "export API_BASE=\"${API_BASE}\""
echo
echo "Key outputs:"
echo "  localstack_endpoint=$(tf_raw localstack_endpoint)"
echo "  api_id=$(tf_raw api_id)"
echo "  api_stage_name=$(tf_raw api_stage_name)"
echo "  input_bucket_name=$(tf_raw input_bucket_name)"
echo "  output_bucket_name=$(tf_raw output_bucket_name)"
echo "  jobs_table_name=$(tf_raw jobs_table_name)"
echo "  work_queue_url=$(tf_raw work_queue_url)"
echo "  work_dlq_url=$(tf_raw work_dlq_url)"
echo "  api_create_job_function_name=$(tf_raw api_create_job_function_name)"
echo "  api_get_job_function_name=$(tf_raw api_get_job_function_name)"
echo "  dispatcher_function_name=$(tf_raw dispatcher_function_name)"
echo "  worker_function_name=$(tf_raw worker_function_name)"
echo
echo "All outputs: terraform -chdir=infra output"
