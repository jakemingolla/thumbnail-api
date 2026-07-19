"""Minimal e2e smoke: LocalStack healthy + Terraform skeleton outputs readable.

Full create → upload → poll → thumbnails coverage lives in later tickets
(THUMB-017/022/025). Dispatcher upload fan-out: ``test_dispatcher.py``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

# Outputs that exist on the current infra skeleton (extend as resources land).
_REQUIRED_OUTPUTS = (
    "localstack_endpoint",
    "input_bucket_name",
    "output_bucket_name",
    "jobs_table_name",
    "work_queue_url",
    "work_dlq_url",
    "api_create_job_role_arn",
    "api_get_job_role_arn",
    "dispatcher_function_name",
    "thumbnail_sizes",
)


@pytest.mark.e2e
def test_localstack_healthy(localstack_endpoint: str) -> None:
    url = f"{localstack_endpoint}/_localstack/health"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 — LocalStack edge only
            status = resp.status
            body = resp.read().decode()
    except urllib.error.URLError as exc:
        pytest.fail(
            f"LocalStack health check failed (fixture=localstack_endpoint).\n"
            f"  endpoint: {localstack_endpoint}\n"
            f"  url: {url}\n"
            f"  error: {exc}"
        )

    assert status == 200, (
        f"LocalStack health returned HTTP {status} at {url} (endpoint={localstack_endpoint})"
    )
    try:
        payload: object = json.loads(body)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"LocalStack health body was not JSON (endpoint={localstack_endpoint}): {exc}\n{body}"
        )
    assert isinstance(payload, dict), (
        f"LocalStack health JSON was not an object (endpoint={localstack_endpoint})"
    )


@pytest.mark.e2e
def test_terraform_outputs_readable(
    tf_outputs: dict[str, object],
    localstack_endpoint: str,
) -> None:
    missing = [name for name in _REQUIRED_OUTPUTS if not tf_outputs.get(name)]
    if missing:
        pytest.fail(
            "Terraform apply outputs incomplete "
            f"(fixture=tf_outputs, endpoint={localstack_endpoint}).\n"
            f"  missing: {missing}\n"
            f"  present: {sorted(tf_outputs)}"
        )

    applied_endpoint = tf_outputs["localstack_endpoint"]
    assert applied_endpoint == localstack_endpoint, (
        "Terraform localstack_endpoint output does not match harness env "
        f"(fixture=tf_outputs).\n"
        f"  harness: {localstack_endpoint}\n"
        f"  output:  {applied_endpoint}"
    )
