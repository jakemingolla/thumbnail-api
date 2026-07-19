"""Unit tests for the S3 ObjectCreated dispatcher Lambda."""

from __future__ import annotations

import json
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from thumbnail_api.config.types import DEFAULT_THUMBNAIL_SIZES, Config
from thumbnail_api.handlers import dispatcher as dispatcher_module
from thumbnail_api.handlers.dispatcher import (
    SQSBatchSendError,
    build_work_message,
    handle_dispatcher,
    handler,
)
from thumbnail_api.jobs.types import (
    JobNotFoundError,
    JobRecord,
    JobStatus,
    JobStatusUpdateResult,
    SizeState,
)

_JOB_ID = "550e8400-e29b-41d4-a716-446655440000"
_INPUT_KEY = f"uploads/{_JOB_ID}/original"
_NOW = "2026-07-18T22:00:00.000Z"


@pytest.fixture
def config() -> Config:
    return Config(
        environment="test",
        input_bucket="thumbnail-input",
        output_bucket="thumbnail-output",
        jobs_table="thumbnail-jobs",
        queue_url="http://127.0.0.1:4566/000000000000/thumbnail-work",
        aws_endpoint_url="http://127.0.0.1:4566",
    )


def _job(*, status: JobStatus = "pending") -> JobRecord:
    size_map: dict[str, SizeState] = {
        str(size): {"status": "pending", "output_key": None} for size in DEFAULT_THUMBNAIL_SIZES
    }
    return JobRecord(
        job_id=_JOB_ID,
        status=status,
        input_key=_INPUT_KEY,
        sizes=size_map,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _s3_event(*keys: str, event_name: str = "ObjectCreated:Put") -> dict[str, Any]:
    return {
        "Records": [
            {
                "eventSource": "aws:s3",
                "eventName": event_name,
                "s3": {
                    "bucket": {"name": "thumbnail-input"},
                    "object": {"key": key},
                },
            }
            for key in keys
        ]
    }


@pytest.fixture
def deps(monkeypatch: pytest.MonkeyPatch, config: Config) -> dict[str, Any]:
    """Wire dispatcher seams: get_job, mark_job_processing, SQS send_message_batch."""
    jobs: dict[str, JobRecord] = {_JOB_ID: _job()}
    sent: list[dict[str, Any]] = []
    mark_calls: list[str] = []
    batch_calls: list[dict[str, Any]] = []

    def fake_get(_client: object, _table: str, job_id: str) -> JobRecord | None:
        return jobs.get(job_id)

    def fake_mark(_client: object, _table: str, job_id: str) -> JobStatusUpdateResult:
        mark_calls.append(job_id)
        job = jobs[job_id]
        if job["status"] == "pending":
            updated = JobRecord(
                job_id=job["job_id"],
                status="processing",
                input_key=job["input_key"],
                sizes=job["sizes"],
                created_at=job["created_at"],
                updated_at=_NOW,
            )
            jobs[job_id] = updated
            return JobStatusUpdateResult(applied=True, job=updated)
        return JobStatusUpdateResult(applied=False, job=job)

    sqs = MagicMock(name="sqs")

    def fake_send_batch(**kwargs: object) -> dict[str, Any]:
        queue_url = kwargs["QueueUrl"]
        entries = cast("list[dict[str, Any]]", kwargs["Entries"])
        batch_calls.append({"queue_url": queue_url, "entry_count": len(entries)})
        sent.extend(
            {
                "queue_url": queue_url,
                "body": json.loads(str(entry["MessageBody"])),
            }
            for entry in entries
        )
        return {
            "Successful": [{"Id": entry["Id"]} for entry in entries],
            "Failed": [],
        }

    sqs.send_message_batch.side_effect = fake_send_batch

    monkeypatch.setattr(
        "thumbnail_api.handlers.dispatcher.get_job",
        fake_get,
    )
    monkeypatch.setattr(
        "thumbnail_api.handlers.dispatcher.mark_job_processing",
        fake_mark,
    )

    return {
        "config": config,
        "sqs_client": sqs,
        "dynamodb_client": MagicMock(name="dynamodb"),
        "jobs": jobs,
        "sent": sent,
        "batch_calls": batch_calls,
        "mark_calls": mark_calls,
    }


def test_build_work_message_matches_schema() -> None:
    assert build_work_message(job_id=_JOB_ID, size=256) == {
        "job_id": _JOB_ID,
        "input_key": _INPUT_KEY,
        "size": 256,
    }


def test_fan_out_one_message_per_configured_size(deps: dict[str, Any]) -> None:
    result = handle_dispatcher(
        _s3_event(_INPUT_KEY),
        config=deps["config"],
        sqs_client=deps["sqs_client"],
        dynamodb_client=deps["dynamodb_client"],
    )

    assert result == {"ok": True}
    assert deps["batch_calls"] == [
        {"queue_url": deps["config"].queue_url, "entry_count": len(DEFAULT_THUMBNAIL_SIZES)}
    ]
    assert [item["body"]["size"] for item in deps["sent"]] == list(DEFAULT_THUMBNAIL_SIZES)
    assert all(item["queue_url"] == deps["config"].queue_url for item in deps["sent"])
    assert all(
        item["body"]
        == {
            "job_id": _JOB_ID,
            "input_key": _INPUT_KEY,
            "size": item["body"]["size"],
        }
        for item in deps["sent"]
    )
    assert deps["mark_calls"] == [_JOB_ID]
    assert deps["jobs"][_JOB_ID]["status"] == "processing"


def test_partial_batch_failure_does_not_mark_processing(deps: dict[str, Any]) -> None:
    def fail_batch(**_kwargs: object) -> dict[str, Any]:
        return {
            "Successful": [{"Id": "0"}],
            "Failed": [
                {
                    "Id": "1",
                    "SenderFault": False,
                    "Code": "InternalError",
                    "Message": "boom",
                }
            ],
        }

    deps["sqs_client"].send_message_batch.side_effect = fail_batch

    with pytest.raises(SQSBatchSendError, match="SendMessageBatch failed"):
        handle_dispatcher(
            _s3_event(_INPUT_KEY),
            config=deps["config"],
            sqs_client=deps["sqs_client"],
            dynamodb_client=deps["dynamodb_client"],
        )

    assert deps["mark_calls"] == []
    assert deps["jobs"][_JOB_ID]["status"] == "pending"


def test_ignores_unexpected_keys(deps: dict[str, Any]) -> None:
    result = handle_dispatcher(
        _s3_event(
            "thumbnails/abc/128.jpg",
            "uploads/only-two-segments",
            "other/prefix/original",
            f"uploads/{_JOB_ID}/original/extra",
        ),
        config=deps["config"],
        sqs_client=deps["sqs_client"],
        dynamodb_client=deps["dynamodb_client"],
    )

    assert result == {"ok": True}
    assert deps["sent"] == []
    assert deps["mark_calls"] == []


def test_ignores_non_object_created_events(deps: dict[str, Any]) -> None:
    handle_dispatcher(
        _s3_event(_INPUT_KEY, event_name="ObjectRemoved:Delete"),
        config=deps["config"],
        sqs_client=deps["sqs_client"],
        dynamodb_client=deps["dynamodb_client"],
    )

    assert deps["sent"] == []
    assert deps["mark_calls"] == []


def test_url_encoded_input_key(deps: dict[str, Any]) -> None:
    encoded = f"uploads%2F{_JOB_ID}%2Foriginal"
    handle_dispatcher(
        _s3_event(encoded),
        config=deps["config"],
        sqs_client=deps["sqs_client"],
        dynamodb_client=deps["dynamodb_client"],
    )

    assert len(deps["sent"]) == len(DEFAULT_THUMBNAIL_SIZES)
    assert deps["mark_calls"] == [_JOB_ID]


def test_duplicate_delivery_re_sends_while_processing(deps: dict[str, Any]) -> None:
    deps["jobs"][_JOB_ID] = _job(status="processing")

    handle_dispatcher(
        _s3_event(_INPUT_KEY),
        config=deps["config"],
        sqs_client=deps["sqs_client"],
        dynamodb_client=deps["dynamodb_client"],
    )

    assert len(deps["sent"]) == len(DEFAULT_THUMBNAIL_SIZES)
    assert deps["mark_calls"] == [_JOB_ID]
    assert deps["jobs"][_JOB_ID]["status"] == "processing"


def test_terminal_job_skips_fan_out(deps: dict[str, Any]) -> None:
    deps["jobs"][_JOB_ID] = _job(status="complete")

    handle_dispatcher(
        _s3_event(_INPUT_KEY),
        config=deps["config"],
        sqs_client=deps["sqs_client"],
        dynamodb_client=deps["dynamodb_client"],
    )

    assert deps["sent"] == []
    assert deps["mark_calls"] == []


def test_missing_job_raises(deps: dict[str, Any]) -> None:
    deps["jobs"].clear()

    with pytest.raises(JobNotFoundError, match="job not found"):
        handle_dispatcher(
            _s3_event(_INPUT_KEY),
            config=deps["config"],
            sqs_client=deps["sqs_client"],
            dynamodb_client=deps["dynamodb_client"],
        )

    assert deps["sent"] == []


def test_handler_wires_config_and_clients(
    monkeypatch: pytest.MonkeyPatch,
    config: Config,
) -> None:
    sqs = MagicMock(name="sqs")
    dynamodb = MagicMock(name="dynamodb")

    def fake_get_config(*, env_file: object = None) -> Config:
        del env_file
        return config

    def fake_get_sqs_client(_config: Config) -> MagicMock:
        return sqs

    def fake_get_dynamodb_client(_config: Config) -> MagicMock:
        return dynamodb

    monkeypatch.setattr(dispatcher_module, "get_config", fake_get_config)
    monkeypatch.setattr(dispatcher_module, "get_sqs_client", fake_get_sqs_client)
    monkeypatch.setattr(dispatcher_module, "get_dynamodb_client", fake_get_dynamodb_client)

    called: dict[str, Any] = {}

    def fake_handle(
        event: dict[str, Any],
        *,
        config: Config,
        sqs_client: object,
        dynamodb_client: object,
    ) -> dict[str, Any]:
        called["event"] = event
        called["config"] = config
        called["sqs_client"] = sqs_client
        called["dynamodb_client"] = dynamodb_client
        return {"ok": True}

    monkeypatch.setattr(dispatcher_module, "handle_dispatcher", fake_handle)

    event = _s3_event(_INPUT_KEY)
    assert handler(event, None) == {"ok": True}
    assert called["event"] is event
    assert called["config"] is config
    assert called["sqs_client"] is sqs
    assert called["dynamodb_client"] is dynamodb
