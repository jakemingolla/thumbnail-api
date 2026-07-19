"""SQS worker — resize one thumbnail size and update job / size status.

Contracts:
- Message body: ``docs/specification/sqs-messages.md`` (THUMB-004)
- Size / job status + retries / DLQ: ``docs/specification/job-state-machine.md`` (THUMB-002)
- Output keys + resize policy: ``docs/specification/s3-keys.md`` (THUMB-003 / THUMB-018)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from botocore.exceptions import ClientError

from thumbnail_api.config import get_config, get_dynamodb_client, get_s3_client
from thumbnail_api.images import ImageProcessingError, resize_to_jpeg
from thumbnail_api.jobs import (
    TERMINAL_SIZE_STATUSES,
    JobNotFoundError,
    claim_size,
    complete_size,
    fail_size,
    get_job,
    size_key,
)
from thumbnail_api.s3 import build_input_key, get_input_object, put_output_object

if TYPE_CHECKING:
    from collections.abc import Sequence

    from botocore.client import BaseClient

    from thumbnail_api.config.types import Config
    from thumbnail_api.jobs.store import DynamoDBClient
    from thumbnail_api.jobs.types import JobRecord

logger = logging.getLogger(__name__)

# Must match docs/specification/job-state-machine.md and var.sqs_max_receive_count.
DEFAULT_MAX_RECEIVE_COUNT = 5

_PERMANENT_S3_ERROR_CODES = frozenset(
    {
        "NoSuchKey",
        "NotFound",
        "404",
        "NoSuchBucket",
    }
)


class MalformedMessageError(ValueError):
    """SQS body failed schema validation; do not update DynamoDB or acknowledge."""


class WorkMessage(NamedTuple):
    """Validated work-queue body fields."""

    job_id: str
    input_key: str
    size: int


class _Deps(NamedTuple):
    """Injected AWS clients + config for one worker invocation."""

    config: Config
    s3_client: BaseClient
    dynamodb_client: DynamoDBClient


def parse_work_message(
    body: object,
    *,
    configured_sizes: Sequence[int],
) -> WorkMessage:
    """Parse and validate a work-queue JSON body per ``sqs-messages.md``.

    Raises:
        MalformedMessageError: Body is not a valid v1 work message.

    """
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8")
    if not isinstance(body, str):
        msg = "MessageBody must be a UTF-8 JSON string"
        raise MalformedMessageError(msg)

    try:
        payload: object = json.loads(body)
    except json.JSONDecodeError as exc:
        msg = "MessageBody is not valid JSON"
        raise MalformedMessageError(msg) from exc

    if not isinstance(payload, dict):
        msg = "MessageBody must be a JSON object"
        raise MalformedMessageError(msg)

    job_id = payload.get("job_id")
    input_key = payload.get("input_key")
    size = payload.get("size")

    if not isinstance(job_id, str) or not job_id or "/" in job_id:
        msg = "job_id must be a non-empty string without '/'"
        raise MalformedMessageError(msg)
    if not isinstance(input_key, str):
        msg = "input_key must be a string"
        raise MalformedMessageError(msg)
    if input_key != build_input_key(job_id):
        msg = "input_key must equal uploads/{job_id}/original"
        raise MalformedMessageError(msg)
    if isinstance(size, bool) or not isinstance(size, int):
        msg = "size must be a JSON integer"
        raise MalformedMessageError(msg)
    if size not in configured_sizes:
        msg = f"size must be one of configured sizes: {list(configured_sizes)}"
        raise MalformedMessageError(msg)

    return WorkMessage(job_id=job_id, input_key=input_key, size=size)


def _receive_count(record: dict[str, Any]) -> int:
    attributes = record.get("attributes")
    if not isinstance(attributes, dict):
        return 1
    raw = attributes.get("ApproximateReceiveCount", "1")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return max(1, raw)
    if not isinstance(raw, str):
        return 1
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _is_permanent_s3_error(exc: ClientError) -> bool:
    code = exc.response.get("Error", {}).get("Code", "")
    return code in _PERMANENT_S3_ERROR_CODES


def _size_status(job: JobRecord, size: int) -> str | None:
    entry = job["sizes"].get(size_key(size))
    if entry is None:
        return None
    return entry["status"]


def _mark_failed_for_dlq(
    deps: _Deps,
    message: WorkMessage,
    *,
    receive_count: int,
    exc: BaseException,
) -> None:
    """Mark size failed on the last receive (caller must re-raise for DLQ redrive)."""
    fail_size(
        deps.dynamodb_client,
        deps.config.jobs_table,
        message.job_id,
        message.size,
        error=f"exhausted retries: {exc}",
    )
    logger.info(
        "Exhausted retries job_id=%s size=%s receive_count=%s; marked failed",
        message.job_id,
        message.size,
        receive_count,
    )


def _process_size(
    message: WorkMessage,
    deps: _Deps,
    *,
    receive_count: int,
    max_receive_count: int,
) -> None:
    """Claim, resize, write, and update status for one size message."""
    job = get_job(deps.dynamodb_client, deps.config.jobs_table, message.job_id)
    if job is None:
        msg = f"job not found: {message.job_id}"
        raise JobNotFoundError(msg)

    current = _size_status(job, message.size)
    if current in TERMINAL_SIZE_STATUSES:
        logger.info(
            "Skipping terminal size job_id=%s size=%s status=%s",
            message.job_id,
            message.size,
            current,
        )
        return

    claim_size(deps.dynamodb_client, deps.config.jobs_table, message.job_id, message.size)

    try:
        original = get_input_object(
            deps.s3_client,
            bucket=deps.config.input_bucket,
            job_id=message.job_id,
        )
        jpeg_bytes = resize_to_jpeg(original.body, message.size)
        output_key = put_output_object(
            deps.s3_client,
            bucket=deps.config.output_bucket,
            job_id=message.job_id,
            size=message.size,
            body=jpeg_bytes,
        )
    except ImageProcessingError as exc:
        fail_size(
            deps.dynamodb_client,
            deps.config.jobs_table,
            message.job_id,
            message.size,
            error=str(exc),
        )
        logger.info(
            "Permanent image failure job_id=%s size=%s: %s",
            message.job_id,
            message.size,
            exc,
        )
        return
    except ClientError as exc:
        if _is_permanent_s3_error(exc):
            fail_size(
                deps.dynamodb_client,
                deps.config.jobs_table,
                message.job_id,
                message.size,
                error=str(exc),
            )
            logger.info(
                "Permanent S3 failure job_id=%s size=%s: %s",
                message.job_id,
                message.size,
                exc,
            )
            return
        if receive_count >= max_receive_count:
            _mark_failed_for_dlq(
                deps,
                message,
                receive_count=receive_count,
                exc=exc,
            )
        raise

    complete_size(
        deps.dynamodb_client,
        deps.config.jobs_table,
        message.job_id,
        message.size,
        output_key,
    )
    logger.info(
        "Completed size job_id=%s size=%s output_key=%s",
        message.job_id,
        message.size,
        output_key,
    )


def handle_worker(
    event: dict[str, Any],
    *,
    config: Config,
    s3_client: BaseClient,
    dynamodb_client: DynamoDBClient,
    max_receive_count: int = DEFAULT_MAX_RECEIVE_COUNT,
) -> dict[str, Any]:
    """Process one SQS work message (event source mapping batch size 1)."""
    records_obj = event.get("Records")
    if not isinstance(records_obj, list):
        msg = "worker expects exactly one SQS record (batch size 1)"
        raise TypeError(msg)
    records = cast("list[object]", records_obj)
    if len(records) != 1:
        msg = "worker expects exactly one SQS record (batch size 1)"
        raise TypeError(msg)

    record_obj = records[0]
    if not isinstance(record_obj, dict):
        msg = "SQS record must be an object"
        raise TypeError(msg)

    record = cast("dict[str, Any]", record_obj)
    message = parse_work_message(
        record.get("body"),
        configured_sizes=config.thumbnail_sizes,
    )
    receive_count = _receive_count(record)
    deps = _Deps(config=config, s3_client=s3_client, dynamodb_client=dynamodb_client)

    try:
        _process_size(
            message,
            deps,
            receive_count=receive_count,
            max_receive_count=max_receive_count,
        )
    except (ClientError, JobNotFoundError, ImageProcessingError):
        # ClientError / image errors already handled (or re-raised) inside _process_size.
        raise
    except Exception as exc:
        # Unexpected infra / runtime blips: retry; on last receive mark failed for DLQ.
        if receive_count >= max_receive_count:
            _mark_failed_for_dlq(
                deps,
                message,
                receive_count=receive_count,
                exc=exc,
            )
        raise

    return {"ok": True}


def handler(event: dict[str, Any], _context: object) -> dict[str, Any]:
    """Lambda entrypoint for SQS → thumbnail worker."""
    config = get_config(env_file=None)
    return handle_worker(
        event,
        config=config,
        s3_client=get_s3_client(config),
        dynamodb_client=cast("DynamoDBClient", get_dynamodb_client(config)),
    )
