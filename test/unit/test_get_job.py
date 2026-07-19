"""Unit tests for the ``GET /jobs/{job_id}`` Lambda handler."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from thumbnail_api.handlers.get_job import handle_get_job, handler
from thumbnail_api.jobs.serde import to_item

if TYPE_CHECKING:
    import pytest

    from thumbnail_api.jobs.types import JobRecord

_TABLE = "jobs"
_JOB_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_OTHER_JOB_ID = "b2c3d4e5-f6a7-8901-bcde-f12345678901"


def _job_record(**overrides: object) -> JobRecord:
    base: JobRecord = {
        "job_id": _JOB_ID,
        "status": "processing",
        "input_key": f"uploads/{_JOB_ID}/original",
        "sizes": {
            "128": {
                "status": "complete",
                "output_key": f"thumbnails/{_JOB_ID}/128.jpg",
            },
            "256": {"status": "processing", "output_key": None},
            "512": {"status": "pending", "output_key": None},
        },
        "created_at": "2026-07-18T22:00:00.000Z",
        "updated_at": "2026-07-18T22:00:05.000Z",
    }
    return cast("JobRecord", {**base, **overrides})


def _event(job_id: str | None) -> dict[str, object]:
    if job_id is None:
        return {"pathParameters": None}
    return {"pathParameters": {"job_id": job_id}}


class FakeDynamoDB:
    """Minimal DynamoDB stand-in that serves ``get_item`` for get-job tests."""

    def __init__(self, items: dict[str, JobRecord] | None = None) -> None:
        """Create an in-memory table optionally seeded with jobs."""
        self.items = items or {}

    def get_item(self, **kwargs: object) -> dict[str, Any]:
        """Return a stored item by key."""
        key = cast("dict[str, Any]", kwargs["Key"])
        job_id = key["job_id"]["S"]
        item = self.items.get(job_id)
        if item is None:
            return {}
        return {"Item": to_item(item)}

    def put_item(self, **kwargs: object) -> dict[str, Any]:
        """Unused by get-job; present to satisfy the DynamoDB client Protocol."""
        raise NotImplementedError

    def update_item(self, **kwargs: object) -> dict[str, Any]:
        """Unused by get-job; present to satisfy the DynamoDB client Protocol."""
        raise NotImplementedError


def test_handle_get_job_returns_job_document() -> None:
    job = _job_record()
    # Optional DynamoDB-only field must not appear on the wire (OpenAPI SizeStatus).
    job["sizes"]["256"]["error"] = "transient"

    response = handle_get_job(
        _event(_JOB_ID),
        client=FakeDynamoDB({_JOB_ID: job}),
        table_name=_TABLE,
    )

    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"] == "application/json"
    body = json.loads(response["body"])
    assert body == {
        "job_id": _JOB_ID,
        "status": "processing",
        "input_key": f"uploads/{_JOB_ID}/original",
        "sizes": {
            "128": {
                "status": "complete",
                "output_key": f"thumbnails/{_JOB_ID}/128.jpg",
            },
            "256": {"status": "processing", "output_key": None},
            "512": {"status": "pending", "output_key": None},
        },
        "created_at": "2026-07-18T22:00:00.000Z",
        "updated_at": "2026-07-18T22:00:05.000Z",
    }
    assert "error" not in body["sizes"]["256"]


def test_handle_get_job_returns_404_when_missing() -> None:
    response = handle_get_job(
        _event(_OTHER_JOB_ID),
        client=FakeDynamoDB({_JOB_ID: _job_record()}),
        table_name=_TABLE,
    )

    assert response["statusCode"] == 404
    assert json.loads(response["body"]) == {
        "error": {"code": "not_found", "message": "Job not found"},
    }


def test_handle_get_job_returns_400_for_invalid_job_id() -> None:
    response = handle_get_job(
        _event("not-a-uuid"),
        client=FakeDynamoDB(),
        table_name=_TABLE,
    )

    assert response["statusCode"] == 400
    assert json.loads(response["body"]) == {
        "error": {"code": "invalid_job_id", "message": "job_id must be a UUID"},
    }


def test_handle_get_job_returns_400_when_path_param_missing() -> None:
    response = handle_get_job(
        _event(None),
        client=FakeDynamoDB(),
        table_name=_TABLE,
    )

    assert response["statusCode"] == 400
    assert json.loads(response["body"])["error"]["code"] == "invalid_job_id"


def test_handler_maps_unexpected_errors_to_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(**_kwargs: object) -> object:
        msg = "config failed"
        raise RuntimeError(msg)

    monkeypatch.setattr("thumbnail_api.handlers.get_job.get_config", boom)

    response = handler(_event(_JOB_ID), None)

    assert response["statusCode"] == 500
    assert json.loads(response["body"]) == {
        "error": {"code": "internal_error", "message": "Unexpected error"},
    }
