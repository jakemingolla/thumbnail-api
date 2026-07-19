"""S3 ``ObjectCreated`` dispatcher — fan out one SQS message per thumbnail size.

Contracts:
- Message body: ``docs/specification/sqs-messages.md`` (THUMB-004)
- ``pending`` → ``processing``: ``docs/specification/job-state-machine.md`` (THUMB-002)
- Input key layout: ``docs/specification/s3-keys.md`` (THUMB-003)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Protocol, cast
from urllib.parse import unquote_plus

from thumbnail_api.config import get_config, get_dynamodb_client, get_sqs_client
from thumbnail_api.jobs import (
    TERMINAL_JOB_STATUSES,
    JobNotFoundError,
    get_job,
    mark_job_processing,
)
from thumbnail_api.s3 import build_input_key, parse_input_key

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thumbnail_api.config.types import Config
    from thumbnail_api.jobs.store import DynamoDBClient
    from thumbnail_api.jobs.types import JobRecord

logger = logging.getLogger(__name__)

# SQS SendMessageBatch accepts at most 10 entries per request.
_SQS_SEND_BATCH_LIMIT = 10


class SQSClient(Protocol):
    """Subset of the boto3 SQS client used by the dispatcher."""

    def send_message_batch(self, **kwargs: object) -> dict[str, Any]:
        """SendMessageBatch."""
        ...


class SQSBatchSendError(RuntimeError):
    """Raised when SQS accepts the call but rejects one or more batch entries."""


def build_work_message(*, job_id: str, size: int) -> dict[str, object]:
    """Return a work-queue body matching ``sqs-messages.md``."""
    return {
        "job_id": job_id,
        "input_key": build_input_key(job_id),
        "size": size,
    }


def _object_key_from_record(record: dict[str, Any]) -> str | None:
    """Return a decoded object key for an S3 ObjectCreated record, or None to skip."""
    event_name = record.get("eventName")
    if not isinstance(event_name, str) or not event_name.startswith("ObjectCreated:"):
        return None

    s3 = record.get("s3")
    if not isinstance(s3, dict):
        return None
    obj = s3.get("object")
    if not isinstance(obj, dict):
        return None
    key = obj.get("key")
    if not isinstance(key, str) or not key:
        return None
    return unquote_plus(key)


def _enqueue_sizes(
    sqs_client: SQSClient,
    *,
    queue_url: str,
    job_id: str,
    sizes: Sequence[int],
) -> None:
    """Enqueue one message per size via SendMessageBatch (chunks of 10)."""
    entries = [
        {
            "Id": str(index),
            "MessageBody": json.dumps(
                build_work_message(job_id=job_id, size=size),
                separators=(",", ":"),
            ),
        }
        for index, size in enumerate(sizes)
    ]
    for start in range(0, len(entries), _SQS_SEND_BATCH_LIMIT):
        chunk = entries[start : start + _SQS_SEND_BATCH_LIMIT]
        response = sqs_client.send_message_batch(
            QueueUrl=queue_url,
            Entries=chunk,
        )
        failed = response.get("Failed") or []
        if failed:
            msg = f"SQS SendMessageBatch failed for job_id={job_id}: {failed}"
            raise SQSBatchSendError(msg)


def _dispatch_input_key(
    input_key: str,
    *,
    config: Config,
    sqs_client: SQSClient,
    dynamodb_client: DynamoDBClient,
) -> None:
    """Fan out work for one input key and move the job to ``processing``."""
    try:
        job_id = parse_input_key(input_key)
    except ValueError:
        logger.info("Ignoring unexpected S3 key: %s", input_key)
        return

    job = get_job(dynamodb_client, config.jobs_table, job_id)
    if job is None:
        msg = f"job not found for input key: {input_key}"
        raise JobNotFoundError(msg)

    if job["status"] in TERMINAL_JOB_STATUSES:
        # Duplicate delivery after completion: do not re-enqueue unbounded work.
        logger.info(
            "Skipping fan-out for terminal job_id=%s status=%s",
            job_id,
            job["status"],
        )
        return

    _enqueue_sizes(
        sqs_client,
        queue_url=config.queue_url,
        job_id=job_id,
        sizes=config.thumbnail_sizes,
    )
    result = mark_job_processing(dynamodb_client, config.jobs_table, job_id)
    applied_job: JobRecord | None = result.job
    logger.info(
        "Dispatched job_id=%s sizes=%s status_applied=%s status=%s",
        job_id,
        list(config.thumbnail_sizes),
        result.applied,
        None if applied_job is None else applied_job["status"],
    )


def handle_dispatcher(
    event: dict[str, Any],
    *,
    config: Config,
    sqs_client: SQSClient,
    dynamodb_client: DynamoDBClient,
) -> dict[str, Any]:
    """Process an S3 notification event: fan out per matching input object."""
    records = event.get("Records")
    if not isinstance(records, list):
        msg = "S3 event must include a Records list"
        raise TypeError(msg)

    for record in records:
        if not isinstance(record, dict):
            continue
        key = _object_key_from_record(cast("dict[str, Any]", record))
        if key is None:
            continue
        _dispatch_input_key(
            key,
            config=config,
            sqs_client=sqs_client,
            dynamodb_client=dynamodb_client,
        )

    return {"ok": True}


def handler(event: dict[str, Any], _context: object) -> dict[str, Any]:
    """Lambda entrypoint for S3 ObjectCreated → SQS fan-out."""
    config = get_config(env_file=None)
    return handle_dispatcher(
        event,
        config=config,
        sqs_client=cast("SQSClient", get_sqs_client(config)),
        dynamodb_client=cast("DynamoDBClient", get_dynamodb_client(config)),
    )
