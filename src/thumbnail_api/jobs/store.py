"""DynamoDB helpers for job records.

Encodes the item shape and conditional updates from
``docs/specification/job-state-machine.md``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from botocore.exceptions import ClientError

from .rollup import compute_job_status, size_key
from .serde import from_item, serialize_value, to_item, utc_now_iso
from .types import (
    JobAlreadyExistsError,
    JobNotFoundError,
    JobRecord,
    JobStatusUpdateResult,
    SizeState,
    SizeUpdateResult,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class DynamoDBClient(Protocol):
    """Subset of the boto3 DynamoDB client used by job helpers."""

    def put_item(self, **kwargs: object) -> dict[str, Any]:
        """PutItem."""
        ...

    def get_item(self, **kwargs: object) -> dict[str, Any]:
        """GetItem."""
        ...

    def update_item(self, **kwargs: object) -> dict[str, Any]:
        """UpdateItem."""
        ...


def put_pending_job(
    client: DynamoDBClient,
    table_name: str,
    *,
    job_id: str,
    input_key: str,
    sizes: Sequence[int],
) -> JobRecord:
    """Create a new job in ``pending`` with every configured size ``pending``.

    Uses ``attribute_not_exists(job_id)`` so a retry with the same id does not
    overwrite an existing record.
    """
    if not job_id:
        msg = "job_id must be non-empty"
        raise ValueError(msg)
    if not input_key:
        msg = "input_key must be non-empty"
        raise ValueError(msg)
    if not sizes:
        msg = "sizes must not be empty"
        raise ValueError(msg)

    timestamp = utc_now_iso()
    size_map: dict[str, SizeState] = {
        size_key(size): {"status": "pending", "output_key": None} for size in sizes
    }
    record = JobRecord(
        job_id=job_id,
        status="pending",
        input_key=input_key,
        sizes=size_map,
        created_at=timestamp,
        updated_at=timestamp,
    )

    try:
        client.put_item(
            TableName=table_name,
            Item=to_item(record),
            ConditionExpression="attribute_not_exists(job_id)",
        )
    except ClientError as exc:
        if _is_conditional_check_failed(exc):
            msg = f"job already exists: {job_id}"
            raise JobAlreadyExistsError(msg) from exc
        raise

    return record


def get_job(
    client: DynamoDBClient,
    table_name: str,
    job_id: str,
) -> JobRecord | None:
    """Return the job record for ``job_id``, or ``None`` if it does not exist."""
    response = client.get_item(
        TableName=table_name,
        Key={"job_id": serialize_value(job_id)},
        ConsistentRead=True,
    )
    item = response.get("Item")
    if item is None:
        return None
    return from_item(item)


def mark_job_processing(
    client: DynamoDBClient,
    table_name: str,
    job_id: str,
) -> JobStatusUpdateResult:
    """Transition overall status ``pending`` → ``processing`` (dispatcher).

    Safe under duplicates: if the job is already ``processing``, ``complete``,
    or ``failed``, returns ``applied=False`` without regressing status.
    """
    timestamp = utc_now_iso()
    try:
        response = client.update_item(
            TableName=table_name,
            Key={"job_id": serialize_value(job_id)},
            UpdateExpression="SET #status = :processing, #updated_at = :updated_at",
            ConditionExpression="attribute_exists(job_id) AND #status = :pending",
            ExpressionAttributeNames={
                "#status": "status",
                "#updated_at": "updated_at",
            },
            ExpressionAttributeValues={
                ":processing": serialize_value("processing"),
                ":pending": serialize_value("pending"),
                ":updated_at": serialize_value(timestamp),
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if not _is_conditional_check_failed(exc):
            raise
        job = get_job(client, table_name, job_id)
        if job is None:
            msg = f"job not found: {job_id}"
            raise JobNotFoundError(msg) from exc
        # Already advanced (or terminal): treat as idempotent success.
        return JobStatusUpdateResult(applied=False, job=job)

    return JobStatusUpdateResult(applied=True, job=from_item(response["Attributes"]))


def claim_size(
    client: DynamoDBClient,
    table_name: str,
    job_id: str,
    size: int | str,
) -> SizeUpdateResult:
    """Claim a size for work: ``pending`` → ``processing``.

    Idempotent under at-least-once delivery:

    - Already ``processing``: ``applied=False``; worker may proceed.
    - Already ``complete`` / ``failed``: ``applied=False``; leave terminal.
    """
    key = size_key(size)
    timestamp = utc_now_iso()

    try:
        response = client.update_item(
            TableName=table_name,
            Key={"job_id": serialize_value(job_id)},
            UpdateExpression="SET #sizes.#size.#status = :processing, #updated_at = :updated_at",
            ConditionExpression=(
                "attribute_exists(job_id) AND "
                "attribute_exists(#sizes.#size) AND #sizes.#size.#status = :pending"
            ),
            ExpressionAttributeNames={
                "#sizes": "sizes",
                "#size": key,
                "#status": "status",
                "#updated_at": "updated_at",
            },
            ExpressionAttributeValues={
                ":processing": serialize_value("processing"),
                ":pending": serialize_value("pending"),
                ":updated_at": serialize_value(timestamp),
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if not _is_conditional_check_failed(exc):
            raise
        return _size_update_noop(client, table_name, job_id)

    return SizeUpdateResult(applied=True, job=from_item(response["Attributes"]))


def complete_size(
    client: DynamoDBClient,
    table_name: str,
    job_id: str,
    size: int | str,
    output_key: str,
) -> SizeUpdateResult:
    """Mark a size ``complete`` and set ``output_key``, then apply job rollup.

    Condition: size status must be ``processing``. Does not overwrite ``failed``
    or change an already-``complete`` size (idempotent no-op).
    """
    if not output_key:
        msg = "output_key must be non-empty"
        raise ValueError(msg)

    key = size_key(size)
    timestamp = utc_now_iso()

    try:
        response = client.update_item(
            TableName=table_name,
            Key={"job_id": serialize_value(job_id)},
            UpdateExpression=(
                "SET #sizes.#size.#status = :complete, "
                "#sizes.#size.#output_key = :output_key, "
                "#updated_at = :updated_at"
            ),
            ConditionExpression=(
                "attribute_exists(job_id) AND attribute_exists(#sizes.#size) AND "
                "#sizes.#size.#status = :processing"
            ),
            ExpressionAttributeNames={
                "#sizes": "sizes",
                "#size": key,
                "#status": "status",
                "#output_key": "output_key",
                "#updated_at": "updated_at",
            },
            ExpressionAttributeValues={
                ":complete": serialize_value("complete"),
                ":processing": serialize_value("processing"),
                ":output_key": serialize_value(output_key),
                ":updated_at": serialize_value(timestamp),
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if not _is_conditional_check_failed(exc):
            raise
        return _size_update_noop(client, table_name, job_id)

    job = from_item(response["Attributes"])
    job = _apply_rollup(client, table_name, job, now=timestamp)
    return SizeUpdateResult(applied=True, job=job)


def fail_size(
    client: DynamoDBClient,
    table_name: str,
    job_id: str,
    size: int | str,
    *,
    error: str | None = None,
) -> SizeUpdateResult:
    """Mark a size ``failed`` (from ``pending`` or ``processing``), then roll up.

    Does not overwrite ``complete`` or an already-``failed`` size.
    """
    key = size_key(size)
    timestamp = utc_now_iso()

    update_expression = (
        "SET #sizes.#size.#status = :failed, "
        "#sizes.#size.#output_key = :null_key, "
        "#updated_at = :updated_at"
    )
    names = {
        "#sizes": "sizes",
        "#size": key,
        "#status": "status",
        "#output_key": "output_key",
        "#updated_at": "updated_at",
    }
    values: dict[str, Any] = {
        ":failed": serialize_value("failed"),
        ":pending": serialize_value("pending"),
        ":processing": serialize_value("processing"),
        ":null_key": serialize_value(None),
        ":updated_at": serialize_value(timestamp),
    }
    if error is not None:
        update_expression += ", #sizes.#size.#error = :error"
        names["#error"] = "error"
        values[":error"] = serialize_value(error)

    try:
        response = client.update_item(
            TableName=table_name,
            Key={"job_id": serialize_value(job_id)},
            UpdateExpression=update_expression,
            ConditionExpression=(
                "attribute_exists(job_id) AND attribute_exists(#sizes.#size) AND "
                "#sizes.#size.#status IN (:pending, :processing)"
            ),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if not _is_conditional_check_failed(exc):
            raise
        return _size_update_noop(client, table_name, job_id)

    job = from_item(response["Attributes"])
    job = _apply_rollup(client, table_name, job, now=timestamp)
    return SizeUpdateResult(applied=True, job=job)


def _apply_rollup(
    client: DynamoDBClient,
    table_name: str,
    job: JobRecord,
    *,
    now: str,
) -> JobRecord:
    """Persist overall status from size map; never leave a terminal overall status."""
    if job["status"] == "pending":
        # Workers must not run before fan-out; do not invent a rollup from pending.
        return job

    # Terminal overall must never regress (e.g. later size completes after a failure).
    if job["status"] in {"complete", "failed"}:
        return job

    desired = compute_job_status(job["sizes"])
    if desired == job["status"]:
        return job

    try:
        response = client.update_item(
            TableName=table_name,
            Key={"job_id": serialize_value(job["job_id"])},
            UpdateExpression="SET #status = :desired, #updated_at = :updated_at",
            ConditionExpression="attribute_exists(job_id) AND #status = :processing",
            ExpressionAttributeNames={
                "#status": "status",
                "#updated_at": "updated_at",
            },
            ExpressionAttributeValues={
                ":desired": serialize_value(desired),
                ":processing": serialize_value("processing"),
                ":updated_at": serialize_value(now),
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if not _is_conditional_check_failed(exc):
            raise
        current = get_job(client, table_name, job["job_id"])
        return current if current is not None else job

    return from_item(response["Attributes"])


def _size_update_noop(
    client: DynamoDBClient,
    table_name: str,
    job_id: str,
) -> SizeUpdateResult:
    job = get_job(client, table_name, job_id)
    if job is None:
        msg = f"job not found: {job_id}"
        raise JobNotFoundError(msg)
    return SizeUpdateResult(applied=False, job=job)


def _is_conditional_check_failed(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"
