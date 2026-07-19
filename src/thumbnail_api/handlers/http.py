"""API Gateway proxy response helpers for the jobs HTTP API."""

from __future__ import annotations

import json
from typing import Any


def json_response(status_code: int, body: dict[str, object]) -> dict[str, Any]:
    """Build an API Gateway proxy integration response with a JSON body."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def error_response(status_code: int, code: str, message: str) -> dict[str, Any]:
    """Build a 4xx/5xx response matching ``docs/specification/api.md`` errors."""
    return json_response(
        status_code,
        {
            "error": {
                "code": code,
                "message": message,
            },
        },
    )
