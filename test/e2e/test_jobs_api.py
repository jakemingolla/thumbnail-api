"""E2E: POST /jobs then GET /jobs/{job_id} via API Gateway (THUMB-017).

Upload and pipeline coverage are later tickets — this scenario only checks the
HTTP create → read path against LocalStack.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, cast
from uuid import UUID

import pytest


def _api_base(tf_outputs: dict[str, object], localstack_endpoint: str) -> str:
    raw = tf_outputs.get("api_base_url")
    if not isinstance(raw, str) or not raw.strip():
        pytest.fail(
            "Terraform output api_base_url missing or empty "
            f"(fixture=tf_outputs, endpoint={localstack_endpoint}).\n"
            f"  present: {sorted(tf_outputs)}"
        )
    return raw.rstrip("/")


def _http_json(
    method: str,
    url: str,
    *,
    body: dict[str, object] | None = None,
    timeout: float = 30,
) -> tuple[int, dict[str, Any]]:
    data: bytes | None = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(  # noqa: S310 — LocalStack e2e only
        url, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            status = resp.status
            raw = resp.read().decode()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read().decode()
    except TimeoutError as exc:
        pytest.fail(
            f"HTTP {method} {url} timed out after {timeout}s "
            f"(Lambda arch mismatch or API Gateway invoke hang?): {exc}"
        )
    except urllib.error.URLError as exc:
        pytest.fail(f"HTTP {method} {url} failed: {exc}")

    try:
        payload: object = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        pytest.fail(f"HTTP {method} {url} returned non-JSON (status={status}): {exc}\n{raw}")

    if not isinstance(payload, dict):
        pytest.fail(f"HTTP {method} {url} JSON was not an object (status={status}): {payload!r}")
    return status, cast("dict[str, Any]", payload)


def _require_nonempty_str(value: object, *, field: str, context: object) -> str:
    if not isinstance(value, str) or not value:
        pytest.fail(f"missing or empty {field}: {context}")
    return value


@pytest.mark.e2e
def test_create_then_get_job(
    tf_outputs: dict[str, object],
    localstack_endpoint: str,
) -> None:
    api_base = _api_base(tf_outputs, localstack_endpoint)

    create_status, created = _http_json(
        "POST",
        f"{api_base}/jobs",
        body={"content_type": "image/jpeg"},
    )
    assert create_status == 201, (
        f"POST /jobs expected 201, got {create_status} "
        f"(api_base={api_base}, endpoint={localstack_endpoint}): {created}"
    )

    job_id = _require_nonempty_str(created.get("job_id"), field="job_id", context=created)
    UUID(job_id)  # raises if not a UUID

    assert created.get("status") == "pending", created
    upload_url = _require_nonempty_str(
        created.get("upload_url"), field="upload_url", context=created
    )
    input_key = _require_nonempty_str(created.get("input_key"), field="input_key", context=created)
    # Image upload must not be an API Gateway route.
    assert "/_aws/execute-api/" not in upload_url, created

    get_status, job = _http_json("GET", f"{api_base}/jobs/{job_id}")
    assert get_status == 200, (
        f"GET /jobs/{{id}} expected 200, got {get_status} "
        f"(api_base={api_base}, job_id={job_id}): {job}"
    )
    assert job.get("job_id") == job_id, job
    assert job.get("status") == "pending", job
    assert job.get("input_key") == input_key, job
    sizes = job.get("sizes")
    assert isinstance(sizes, dict), job
    assert sizes, job
