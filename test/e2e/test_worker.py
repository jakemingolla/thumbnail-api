"""E2E: one work-queue size message → output object + per-size ``complete``.

Full multi-size job rollup is covered later (THUMB-025). This scenario invokes
the worker Lambda with a synthetic SQS event (batch size 1) against LocalStack.
"""

from __future__ import annotations

import json
import time
import uuid
from io import BytesIO
from typing import TYPE_CHECKING, Any, cast

import boto3
import pytest
from botocore.config import Config as BotoConfig
from PIL import Image

from thumbnail_api.jobs import get_job, mark_job_processing, put_pending_job
from thumbnail_api.s3 import build_input_key, build_output_key

if TYPE_CHECKING:
    from thumbnail_api.jobs.store import DynamoDBClient

_SIZE = 256
_THUMBNAIL_SIZES = [128, 256, 512]


def _png_bytes(width: int = 320, height: int = 240) -> bytes:
    image = Image.new("RGB", (width, height), color=(10, 100, 200))
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _boto_clients(
    *,
    endpoint: str,
    region: str,
    access_key: str,
    secret_key: str,
) -> tuple[Any, DynamoDBClient, Any]:
    common = {
        "endpoint_url": endpoint,
        "region_name": region,
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
    }
    s3 = boto3.client(
        "s3",
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
        **common,
    )
    dynamodb = cast("DynamoDBClient", boto3.client("dynamodb", **common))
    lambda_client = boto3.client("lambda", **common)
    return s3, dynamodb, lambda_client


@pytest.mark.e2e
def test_worker_one_size_writes_output_and_completes(
    tf_outputs: dict[str, object],
    localstack_endpoint: str,
    aws_credentials: dict[str, str],
) -> None:
    worker_fn = tf_outputs.get("worker_function_name")
    input_bucket = tf_outputs.get("input_bucket_name")
    output_bucket = tf_outputs.get("output_bucket_name")
    jobs_table = tf_outputs.get("jobs_table_name")
    if not all(
        isinstance(value, str) and value
        for value in (worker_fn, input_bucket, output_bucket, jobs_table)
    ):
        pytest.fail(
            "Terraform outputs missing worker / bucket / table names "
            f"(endpoint={localstack_endpoint}). present={sorted(tf_outputs)}"
        )

    worker_fn = cast("str", worker_fn)
    input_bucket = cast("str", input_bucket)
    output_bucket = cast("str", output_bucket)
    jobs_table = cast("str", jobs_table)

    s3, dynamodb, lambda_client = _boto_clients(
        endpoint=localstack_endpoint,
        region=aws_credentials["region_name"],
        access_key=aws_credentials["aws_access_key_id"],
        secret_key=aws_credentials["aws_secret_access_key"],
    )

    job_id = str(uuid.uuid4())
    input_key = build_input_key(job_id)
    output_key = build_output_key(job_id, _SIZE)

    # Seed job + original; dispatcher is out of scope — mark processing directly.
    put_pending_job(
        dynamodb,
        jobs_table,
        job_id=job_id,
        input_key=input_key,
        sizes=_THUMBNAIL_SIZES,
    )
    mark_job_processing(dynamodb, jobs_table, job_id)
    s3.put_object(
        Bucket=input_bucket,
        Key=input_key,
        Body=_png_bytes(),
        ContentType="image/png",
    )

    event = {
        "Records": [
            {
                "messageId": "e2e-worker-1",
                "body": json.dumps(
                    {"job_id": job_id, "input_key": input_key, "size": _SIZE},
                    separators=(",", ":"),
                ),
                "attributes": {"ApproximateReceiveCount": "1"},
                "eventSource": "aws:sqs",
            }
        ]
    }

    response = lambda_client.invoke(
        FunctionName=worker_fn,
        InvocationType="RequestResponse",
        Payload=json.dumps(event).encode("utf-8"),
    )
    payload_stream = response["Payload"]
    payload_raw = cast("bytes", payload_stream.read()).decode("utf-8")
    if response.get("FunctionError"):
        pytest.fail(
            f"worker Lambda failed (endpoint={localstack_endpoint}): "
            f"{response.get('FunctionError')}\n{payload_raw}"
        )

    payload: object = json.loads(payload_raw)
    assert payload == {"ok": True}, payload

    # LocalStack S3/DynamoDB are usually immediate; short poll for eventual consistency.
    deadline = time.monotonic() + 15
    job = None
    while time.monotonic() < deadline:
        job = get_job(dynamodb, jobs_table, job_id)
        if job is not None and job["sizes"][str(_SIZE)]["status"] == "complete":
            break
        time.sleep(0.25)

    assert job is not None
    size_state = job["sizes"][str(_SIZE)]
    assert size_state["status"] == "complete", job
    assert size_state["output_key"] == output_key

    head = s3.head_object(Bucket=output_bucket, Key=output_key)
    assert head["ContentType"] == "image/jpeg"
    content_length = head["ContentLength"]
    assert isinstance(content_length, int)
    assert content_length > 0


@pytest.mark.e2e
def test_work_queue_redrive_to_dlq_configured(
    tf_outputs: dict[str, object],
    localstack_endpoint: str,
    aws_credentials: dict[str, str],
) -> None:
    """Poison messages can reach the DLQ after maxReceiveCount (config check)."""
    queue_url = tf_outputs.get("work_queue_url")
    dlq_arn = tf_outputs.get("work_dlq_arn")
    max_receive = tf_outputs.get("sqs_max_receive_count")
    if not isinstance(queue_url, str) or not queue_url:
        pytest.fail(f"work_queue_url missing (endpoint={localstack_endpoint})")
    if not isinstance(dlq_arn, str) or not dlq_arn:
        pytest.fail(f"work_dlq_arn missing (endpoint={localstack_endpoint})")
    if max_receive != 5:
        pytest.fail(
            f"sqs_max_receive_count expected 5, got {max_receive!r} "
            f"(endpoint={localstack_endpoint})"
        )

    sqs = boto3.client(
        "sqs",
        endpoint_url=localstack_endpoint,
        region_name=aws_credentials["region_name"],
        aws_access_key_id=aws_credentials["aws_access_key_id"],
        aws_secret_access_key=aws_credentials["aws_secret_access_key"],
    )
    attrs = sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["RedrivePolicy"],
    )["Attributes"]
    redrive_raw = attrs["RedrivePolicy"]
    assert isinstance(redrive_raw, str)
    policy: dict[str, Any] = json.loads(redrive_raw)
    assert policy["deadLetterTargetArn"] == dlq_arn
    assert int(policy["maxReceiveCount"]) == 5
