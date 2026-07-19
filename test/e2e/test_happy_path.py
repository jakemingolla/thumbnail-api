"""E2E: full customer path — create → presigned PUT → poll → complete (THUMB-025).

Runs on the THUMB-028 harness (``just test-e2e``). Asserts overall ``complete``
and that each configured size's ``output_key`` object exists in the output bucket.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from io import BytesIO
from typing import Any, cast
from urllib.parse import urlparse, urlunparse
from uuid import UUID

import boto3
import pytest
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from PIL import Image

_TERMINAL_STATUSES = frozenset({"complete", "failed"})
_POLL_TIMEOUT_SECONDS = 120.0
_POLL_INTERVAL_SECONDS = 1.0
_CONTENT_TYPE = "image/jpeg"

_PATH_STYLE_S3 = BotoConfig(
    signature_version="s3v4",
    s3={"addressing_style": "path"},
)


def _api_base(tf_outputs: dict[str, object], localstack_endpoint: str) -> str:
    raw = tf_outputs.get("api_base_url")
    if not isinstance(raw, str) or not raw.strip():
        pytest.fail(
            "Terraform output api_base_url missing or empty "
            f"(fixture=tf_outputs, endpoint={localstack_endpoint}).\n"
            f"  present: {sorted(tf_outputs)}"
        )
    return raw.rstrip("/")


def _require_nonempty_str(value: object, *, field: str, context: object) -> str:
    if not isinstance(value, str) or not value:
        pytest.fail(f"missing or empty {field}: {context}")
    return value


def _require_sizes(tf_outputs: dict[str, object]) -> list[int]:
    value = tf_outputs.get("thumbnail_sizes")
    if not isinstance(value, list) or not value:
        pytest.fail(f"tf_outputs['thumbnail_sizes'] missing or empty: {value!r}")
    sizes: list[int] = []
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
            pytest.fail(f"tf_outputs['thumbnail_sizes'] has invalid entry: {item!r}")
        sizes.append(item)
    return sizes


def _jpeg_bytes(width: int = 640, height: int = 480) -> bytes:
    image = Image.new("RGB", (width, height), color=(40, 120, 200))
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


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


def _host_reachable_upload_url(upload_url: str, localstack_endpoint: str) -> str:
    """Rewrite in-Lambda LocalStack edge host to the harness host endpoint.

    ``create_job`` signs URLs against ``AWS_ENDPOINT_URL`` inside Lambda
    (``http://localhost.localstack.cloud:4566``). Host clients must PUT against
    ``LOCALSTACK_ENDPOINT`` (``http://127.0.0.1:<edge-port>``). LocalStack accepts
    the host swap for path-style S3; do not rewrite to virtual-hosted style.
    """
    parsed = urlparse(upload_url)
    endpoint = urlparse(localstack_endpoint)
    if not endpoint.netloc:
        pytest.fail(f"LOCALSTACK_ENDPOINT has no netloc: {localstack_endpoint!r}")
    if parsed.netloc == endpoint.netloc:
        return upload_url
    rewritten = urlunparse(
        parsed._replace(scheme=endpoint.scheme or "http", netloc=endpoint.netloc)
    )
    print(f"e2e: rewrote upload_url host {parsed.netloc!r} → {endpoint.netloc!r}")
    return rewritten


def _http_put(
    url: str,
    *,
    body: bytes,
    content_type: str,
    timeout: float = 30,
) -> int:
    request = urllib.request.Request(  # noqa: S310 — LocalStack e2e only
        url,
        data=body,
        headers={"Content-Type": content_type},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode(errors="replace")
        pytest.fail(f"HTTP PUT upload failed status={exc.code} url={url}\n{err_body}")
    except TimeoutError as exc:
        pytest.fail(f"HTTP PUT upload timed out after {timeout}s url={url}: {exc}")
    except urllib.error.URLError as exc:
        pytest.fail(f"HTTP PUT upload failed url={url}: {exc}")


def _poll_until_terminal(
    *,
    api_base: str,
    job_id: str,
    timeout_seconds: float = _POLL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    last_status_code: int | None = None
    while time.monotonic() < deadline:
        status_code, job = _http_json("GET", f"{api_base}/jobs/{job_id}")
        last_status_code = status_code
        last = job
        if status_code == 200 and job.get("status") in _TERMINAL_STATUSES:
            return job
        time.sleep(_POLL_INTERVAL_SECONDS)

    job_json = json.dumps(last, indent=2, default=str) if last is not None else "<no response>"
    pytest.fail(
        f"job {job_id} did not reach a terminal status within {timeout_seconds}s "
        f"(api_base={api_base}, last_http={last_status_code}).\n"
        f"Last GET /jobs/{{id}} body:\n{job_json}"
    )


@pytest.mark.e2e
def test_create_upload_poll_complete(
    tf_outputs: dict[str, object],
    localstack_endpoint: str,
    aws_credentials: dict[str, str],
) -> None:
    api_base = _api_base(tf_outputs, localstack_endpoint)
    sizes = _require_sizes(tf_outputs)
    output_bucket = tf_outputs.get("output_bucket_name")
    if not isinstance(output_bucket, str) or not output_bucket:
        pytest.fail(
            f"tf_outputs['output_bucket_name'] missing "
            f"(endpoint={localstack_endpoint}). present={sorted(tf_outputs)}"
        )

    create_status, created = _http_json(
        "POST",
        f"{api_base}/jobs",
        body={"content_type": _CONTENT_TYPE},
    )
    assert create_status == 201, (
        f"POST /jobs expected 201, got {create_status} "
        f"(api_base={api_base}, endpoint={localstack_endpoint}): {created}"
    )

    job_id = _require_nonempty_str(created.get("job_id"), field="job_id", context=created)
    UUID(job_id)
    assert created.get("status") == "pending", created
    upload_url = _require_nonempty_str(
        created.get("upload_url"), field="upload_url", context=created
    )
    input_key = _require_nonempty_str(created.get("input_key"), field="input_key", context=created)
    assert "/_aws/execute-api/" not in upload_url, created
    assert input_key == f"uploads/{job_id}/original", created

    put_url = _host_reachable_upload_url(upload_url, localstack_endpoint)
    put_status = _http_put(put_url, body=_jpeg_bytes(), content_type=_CONTENT_TYPE)
    assert put_status in {200, 204}, (
        f"presigned PUT expected 200/204, got {put_status} (job_id={job_id}, put_url={put_url})"
    )

    job = _poll_until_terminal(api_base=api_base, job_id=job_id)

    assert job.get("job_id") == job_id, job
    assert job.get("status") == "complete", (
        f"expected overall status complete, got {job.get('status')!r}.\n"
        f"job JSON:\n{json.dumps(job, indent=2, default=str)}"
    )
    assert job.get("input_key") == input_key, job

    sizes_raw = job.get("sizes")
    if not isinstance(sizes_raw, dict):
        pytest.fail(f"job.sizes must be an object: {job}")
    size_map = cast("dict[str, Any]", sizes_raw)
    expected_size_keys = {str(size) for size in sizes}
    assert set(size_map) == expected_size_keys, (
        f"sizes keys mismatch: job={sorted(size_map)} expected={sorted(expected_size_keys)}\n"
        f"job JSON:\n{json.dumps(job, indent=2, default=str)}"
    )

    s3 = boto3.client(
        "s3",
        config=_PATH_STYLE_S3,
        endpoint_url=localstack_endpoint,
        region_name=aws_credentials["region_name"],
        aws_access_key_id=aws_credentials["aws_access_key_id"],
        aws_secret_access_key=aws_credentials["aws_secret_access_key"],
    )

    for size in sizes:
        entry = size_map[str(size)]
        assert isinstance(entry, dict), job
        assert entry.get("status") == "complete", (
            f"size {size} not complete.\njob JSON:\n{json.dumps(job, indent=2, default=str)}"
        )
        output_key = entry.get("output_key")
        if not isinstance(output_key, str) or not output_key:
            pytest.fail(
                f"size {size} missing output_key.\n"
                f"job JSON:\n{json.dumps(job, indent=2, default=str)}"
            )
        expected_key = f"thumbnails/{job_id}/{size}.jpg"
        assert output_key == expected_key, (
            f"size {size} output_key={output_key!r} expected={expected_key!r}"
        )
        try:
            head = s3.head_object(Bucket=output_bucket, Key=output_key)
        except ClientError as exc:
            pytest.fail(
                f"output object missing for size {size}: "
                f"s3://{output_bucket}/{output_key} "
                f"(endpoint={localstack_endpoint}): {exc}\n"
                f"job JSON:\n{json.dumps(job, indent=2, default=str)}"
            )
        assert head.get("ContentType") == "image/jpeg", head
        content_length = head.get("ContentLength")
        assert isinstance(content_length, int), head
        assert content_length > 0, head
