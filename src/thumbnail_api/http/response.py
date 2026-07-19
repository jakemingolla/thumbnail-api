"""API Gateway Lambda proxy JSON responses for the jobs API."""

from __future__ import annotations

import json
from typing import Any


def json_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    """Return an API Gateway proxy response with a JSON body."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, separators=(",", ":")),
    }


def error_response(status_code: int, code: str, message: str) -> dict[str, Any]:
    """Return a jobs-API error body: ``{"error": {"code", "message"}}``."""
    return json_response(
        status_code,
        {
            "error": {
                "code": code,
                "message": message,
            }
        },
    )
