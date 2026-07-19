"""E2E: S3 ObjectCreated under uploads/ → dispatcher → N SQS messages + processing."""

from __future__ import annotations

import json
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

    def receive_message(self, **kwargs: object) -> dict[str, Any]:
        """ReceiveMessage."""
        ...

    def delete_message(self, **kwargs: object) -> dict[str, Any]:
        """DeleteMessage."""
        ...

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


def _collect_messages(
    sqs: SQSClient,
    *,
    queue_url: str,
    expected: int,
    timeout_seconds: float = _POLL_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    collected: list[dict[str, Any]] = []
    while len(collected) < expected and time.monotonic() < deadline:
        response = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=min(10, expected - len(collected)),
            WaitTimeSeconds=1,
            VisibilityTimeout=30,
        )
        messages = cast("list[dict[str, Any]]", response.get("Messages") or [])
        for message in messages:
            collected.append(message)
            receipt = message.get("ReceiptHandle")
            if isinstance(receipt, str):
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
        if len(collected) < expected:
            time.sleep(_POLL_INTERVAL_SECONDS)
    return collected


def _wait_for_processing(
    dynamodb: DynamoDBClient,
    *,
    table_name: str,
    job_id: str,
    timeout_seconds: float = _POLL_TIMEOUT_SECONDS,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    last_status = "missing"
    while time.monotonic() < deadline:
        job = get_job(dynamodb, table_name, job_id)
        if job is not None:
            last_status = job["status"]
            if last_status == "processing":
                return last_status
        time.sleep(_POLL_INTERVAL_SECONDS)
    pytest.fail(
        f"job {job_id} did not reach processing within {timeout_seconds}s "
        f"(last_status={last_status})"
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

    messages = _collect_messages(sqs, queue_url=queue_url, expected=len(sizes))
    if len(messages) != len(sizes):
        pytest.fail(
            "dispatcher did not enqueue one SQS message per configured size "
            f"(dispatcher={dispatcher}, job_id={job_id}).\n"
            f"  expected: {len(sizes)} (sizes={sizes})\n"
            f"  received: {len(messages)}\n"
            f"  bodies: {[m.get('Body') for m in messages]}"
        )

    parsed_sizes: set[int] = set()
    for message in messages:
        body_raw = message.get("Body")
        if not isinstance(body_raw, str):
            pytest.fail(f"SQS message missing Body string: {message!r}")
        body: object = json.loads(body_raw)
        if not isinstance(body, dict):
            pytest.fail(f"SQS body was not a JSON object: {body_raw!r}")
        assert body.get("job_id") == job_id
        assert body.get("input_key") == input_key
        size = body.get("size")
        if not isinstance(size, int) or isinstance(size, bool):
            pytest.fail(f"SQS body size is not an int: {body!r}")
        parsed_sizes.add(size)

    assert parsed_sizes == set(sizes), (
        f"SQS sizes mismatch (job_id={job_id}): got {sorted(parsed_sizes)} want {sizes}"
    )

    status = _wait_for_processing(dynamodb, table_name=table, job_id=job_id)
    assert status == "processing"
