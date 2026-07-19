"""E2E: S3 ObjectCreated under uploads/ → dispatcher → fan-out + processing.

With the worker SQS event source mapping enabled, messages may be consumed
before this test can receive them. Fan-out is asserted via DynamoDB: every
configured size leaves ``pending`` (worker claimed the message), and overall
status leaves ``pending`` (dispatcher ``mark_job_processing``).
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any, Protocol, cast

import boto3
import pytest
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from thumbnail_api.jobs import get_job, put_pending_job
from thumbnail_api.s3 import build_input_key

if TYPE_CHECKING:
    from thumbnail_api.jobs.store import DynamoDBClient
    from thumbnail_api.jobs.types import JobRecord

_PATH_STYLE_S3 = BotoConfig(
    signature_version="s3v4",
    s3={"addressing_style": "path"},
)

# LocalStack S3 → Lambda notification can take a few seconds.
_POLL_TIMEOUT_SECONDS = 45.0
_POLL_INTERVAL_SECONDS = 1.0

_PURGE_IN_PROGRESS = "AWS.SimpleQueueService.PurgeQueueInProgress"


class SQSClient(Protocol):
    """Subset of boto3 SQS used by the dispatcher e2e scenario."""

    def purge_queue(self, **kwargs: object) -> dict[str, Any]:
        """PurgeQueue."""
        ...


def _require_str(tf_outputs: dict[str, object], key: str) -> str:
    value = tf_outputs.get(key)
    if not isinstance(value, str) or not value:
        pytest.fail(f"tf_outputs[{key!r}] missing or not a non-empty string: {value!r}")
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


def _wait_for_fan_out(
    dynamodb: DynamoDBClient,
    *,
    table_name: str,
    job_id: str,
    sizes: list[int],
    timeout_seconds: float = _POLL_TIMEOUT_SECONDS,
) -> JobRecord:
    """Wait until dispatcher advanced the job and every size left ``pending``."""
    deadline = time.monotonic() + timeout_seconds
    last: JobRecord | None = None
    while time.monotonic() < deadline:
        job = get_job(dynamodb, table_name, job_id)
        last = job
        if job is not None and job["status"] != "pending":
            pending_sizes = [
                str(size)
                for size in sizes
                if (entry := job["sizes"].get(str(size))) is None or entry["status"] == "pending"
            ]
            if not pending_sizes:
                return job
        time.sleep(_POLL_INTERVAL_SECONDS)

    size_summary = None
    if last is not None:
        size_summary = {key: entry["status"] for key, entry in last["sizes"].items()}
    pytest.fail(
        f"dispatcher fan-out not observed for job {job_id} within {timeout_seconds}s "
        f"(last_status={None if last is None else last['status']}, sizes={size_summary}). "
        "Expected overall status ≠ pending and every configured size ≠ pending "
        "(worker ESM may consume SQS messages before a test receive)."
    )


@pytest.mark.e2e
def test_s3_put_fans_out_sqs_and_marks_processing(
    localstack_endpoint: str,
    aws_credentials: dict[str, str],
    tf_outputs: dict[str, object],
) -> None:
    bucket = _require_str(tf_outputs, "input_bucket_name")
    table = _require_str(tf_outputs, "jobs_table_name")
    queue_url = _require_str(tf_outputs, "work_queue_url")
    sizes = _require_sizes(tf_outputs)
    dispatcher = _require_str(tf_outputs, "dispatcher_function_name")

    session_kwargs = {
        "aws_access_key_id": aws_credentials["aws_access_key_id"],
        "aws_secret_access_key": aws_credentials["aws_secret_access_key"],
        "region_name": aws_credentials["region_name"],
        "endpoint_url": localstack_endpoint,
    }
    s3 = boto3.client("s3", config=_PATH_STYLE_S3, **session_kwargs)
    dynamodb = cast("DynamoDBClient", boto3.client("dynamodb", **session_kwargs))
    sqs = cast("SQSClient", boto3.client("sqs", **session_kwargs))

    try:
        sqs.purge_queue(QueueUrl=queue_url)
        # Purge is eventually consistent; brief pause avoids mixing old messages.
        time.sleep(1.0)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code != _PURGE_IN_PROGRESS:
            raise
        time.sleep(2.0)

    job_id = str(uuid.uuid4())
    input_key = build_input_key(job_id)
    put_pending_job(
        dynamodb,
        table,
        job_id=job_id,
        input_key=input_key,
        sizes=sizes,
    )

    s3.put_object(
        Bucket=bucket,
        Key=input_key,
        Body=b"fake-jpeg-bytes",
        ContentType="image/jpeg",
    )

    job = _wait_for_fan_out(
        dynamodb,
        table_name=table,
        job_id=job_id,
        sizes=sizes,
    )

    # Dispatcher must leave pending; workers may already have rolled up to failed
    # on permanent image errors from the fake upload body.
    assert job["status"] in {"processing", "failed"}, (
        f"unexpected overall status after fan-out (dispatcher={dispatcher}, "
        f"job_id={job_id}): {job['status']}"
    )
    for size in sizes:
        size_status = job["sizes"][str(size)]["status"]
        assert size_status != "pending", (
            f"size {size} still pending after fan-out (job_id={job_id}, job={job})"
        )
