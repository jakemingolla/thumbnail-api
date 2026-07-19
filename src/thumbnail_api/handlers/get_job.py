"""``GET /jobs/{job_id}`` Lambda handler (API Gateway REST proxy)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, cast

from thumbnail_api.config.clients import get_dynamodb_client
from thumbnail_api.config.main import get_config
from thumbnail_api.handlers.http import error_response, json_response
from thumbnail_api.jobs.store import get_job

if TYPE_CHECKING:
    from collections.abc import Mapping

    from thumbnail_api.jobs.store import DynamoDBClient
    from thumbnail_api.jobs.types import JobRecord, SizeState


def handler(event: dict[str, Any], _context: object) -> dict[str, Any]:
    """Lambda entrypoint: load config, read the job, return an API Gateway response."""
    try:
        config = get_config(env_file=None)
        return handle_get_job(
            event,
            client=cast("DynamoDBClient", get_dynamodb_client(config)),
            table_name=config.jobs_table,
        )
    except Exception:  # noqa: BLE001 - API contract: unexpected failures → JSON 500
        return error_response(500, "internal_error", "Unexpected error")


def handle_get_job(
    event: Mapping[str, object],
    *,
    client: DynamoDBClient,
    table_name: str,
) -> dict[str, Any]:
    """Resolve ``job_id`` from the path, load the job, and shape the HTTP response."""
    job_id = _job_id_from_event(event)
    if job_id is None:
        return error_response(400, "invalid_job_id", "job_id must be a UUID")

    job = get_job(client, table_name, job_id)
    if job is None:
        return error_response(404, "not_found", "Job not found")

    return json_response(200, _job_to_response(job))


def _job_id_from_event(event: Mapping[str, object]) -> str | None:
    path_parameters = event.get("pathParameters")
    if not isinstance(path_parameters, dict):
        return None
    raw = path_parameters.get("job_id")
    if not isinstance(raw, str) or not _is_uuid(raw):
        return None
    return raw


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _job_to_response(job: JobRecord) -> dict[str, object]:
    """Project a DynamoDB job record to the public ``Job`` wire shape."""
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "input_key": job["input_key"],
        "sizes": {label: _size_to_response(size) for label, size in job["sizes"].items()},
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }


def _size_to_response(size: SizeState) -> dict[str, object]:
    # OpenAPI SizeStatus allows only status + output_key (not optional DynamoDB error).
    return {
        "status": size["status"],
        "output_key": size["output_key"],
    }
