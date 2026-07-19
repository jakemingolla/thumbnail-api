"""``POST /jobs`` — create a pending job and return a presigned upload URL.

Contract: ``docs/specification/api.md`` (THUMB-001).
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any, cast

from thumbnail_api.config import get_config, get_dynamodb_client, get_s3_client
from thumbnail_api.http import error_response, json_response
from thumbnail_api.jobs import put_pending_job
from thumbnail_api.s3 import (
    ALLOWED_UPLOAD_CONTENT_TYPES,
    build_input_key,
    generate_presigned_put_url,
)

if TYPE_CHECKING:
    from botocore.client import BaseClient

    from thumbnail_api.config.types import Config
    from thumbnail_api.jobs.store import DynamoDBClient

logger = logging.getLogger(__name__)

_ALLOWED_CONTENT_TYPES_MESSAGE = "content_type must be one of " + ", ".join(
    sorted(ALLOWED_UPLOAD_CONTENT_TYPES)
)


def _new_job_id() -> str:
    return str(uuid.uuid4())


def _header_value(headers: object, name: str) -> str | None:
    if not isinstance(headers, dict):
        return None
    target = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == target:
            if isinstance(value, str):
                return value
            if isinstance(value, list) and value and isinstance(value[0], str):
                return value[0]
            return None
    return None


def _is_application_json(content_type: str | None) -> bool:
    if content_type is None or not content_type.strip():
        return False
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "application/json"


def _raw_body(event: dict[str, Any]) -> str:
    body = event.get("body")
    if body is None:
        return ""
    if not isinstance(body, str):
        msg = "Request body must be a string"
        raise TypeError(msg)
    if event.get("isBase64Encoded"):
        return base64.b64decode(body).decode("utf-8")
    return body


def _content_type_from_object(body: dict[str, Any]) -> str | dict[str, Any]:
    """Return validated upload content_type, or an error response."""
    unknown = sorted(str(key) for key in body if key != "content_type")
    if unknown:
        fields = ", ".join(unknown)
        return error_response(
            400,
            "invalid_request",
            f"Unknown field(s): {fields}",
        )

    if "content_type" not in body:
        return error_response(
            400,
            "invalid_request",
            "content_type is required",
        )

    content_type = body["content_type"]
    if not isinstance(content_type, str):
        return error_response(
            400,
            "invalid_request",
            "content_type must be a string",
        )

    if content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
        return error_response(
            400,
            "unsupported_content_type",
            _ALLOWED_CONTENT_TYPES_MESSAGE,
        )

    return content_type


def _parse_request(event: dict[str, Any]) -> str | dict[str, Any]:
    """Validate the proxy event; return upload content_type or an error response."""
    headers = event.get("headers")
    content_type_header = _header_value(headers, "content-type")
    if content_type_header is None:
        content_type_header = _header_value(event.get("multiValueHeaders"), "content-type")

    if not _is_application_json(content_type_header):
        return error_response(
            415,
            "unsupported_media_type",
            "Content-Type must be application/json",
        )

    try:
        raw_body = _raw_body(event)
    except (TypeError, UnicodeDecodeError, ValueError):
        return error_response(
            400,
            "invalid_json",
            "Request body must be valid JSON",
        )

    if raw_body.strip() == "":
        return error_response(
            400,
            "invalid_json",
            "Request body must be valid JSON",
        )

    try:
        parsed: object = json.loads(raw_body)
    except json.JSONDecodeError:
        return error_response(
            400,
            "invalid_json",
            "Request body must be valid JSON",
        )

    if not isinstance(parsed, dict):
        return error_response(
            400,
            "invalid_request",
            "Request body must be a JSON object",
        )

    return _content_type_from_object(cast("dict[str, Any]", parsed))


def handle_create_job(
    event: dict[str, Any],
    *,
    config: Config,
    s3_client: BaseClient,
    dynamodb_client: DynamoDBClient,
) -> dict[str, Any]:
    """Create a pending job and return a presigned PUT URL for the input object."""
    parsed = _parse_request(event)
    if isinstance(parsed, dict):
        return parsed
    upload_content_type = parsed

    job_id = _new_job_id()
    input_key = build_input_key(job_id)
    try:
        record = put_pending_job(
            dynamodb_client,
            config.jobs_table,
            job_id=job_id,
            input_key=input_key,
            sizes=config.thumbnail_sizes,
        )
        upload = generate_presigned_put_url(
            s3_client,
            bucket=config.input_bucket,
            job_id=job_id,
            content_type=upload_content_type,
        )
    except Exception:
        logger.exception("create_job failed for job_id=%s", job_id)
        return error_response(500, "internal_error", "Unexpected error")

    return json_response(
        201,
        {
            "job_id": record["job_id"],
            "upload_url": upload.url,
            "input_key": upload.key,
            "status": record["status"],
        },
    )


def handler(event: dict[str, Any], _context: object) -> dict[str, Any]:
    """Handle API Gateway Lambda proxy events for ``POST /jobs``."""
    try:
        config = get_config(env_file=None)
        return handle_create_job(
            event,
            config=config,
            s3_client=get_s3_client(config),
            dynamodb_client=cast("DynamoDBClient", get_dynamodb_client(config)),
        )
    except Exception:
        logger.exception("create_job handler failed before request handling")
        return error_response(500, "internal_error", "Unexpected error")
