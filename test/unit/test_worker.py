"""Unit tests for the SQS thumbnail worker Lambda."""

from __future__ import annotations

import json
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from PIL import Image

from thumbnail_api.config.types import DEFAULT_THUMBNAIL_SIZES, Config
from thumbnail_api.handlers import worker as worker_module
from thumbnail_api.handlers.worker import (
    DEFAULT_MAX_RECEIVE_COUNT,
    MalformedMessageError,
    handle_worker,
    handler,
    parse_work_message,
)
from thumbnail_api.images import ImageProcessingError
from thumbnail_api.jobs.types import (
    JobNotFoundError,
    JobRecord,
    JobStatus,
    SizeState,
    SizeStatus,
    SizeUpdateResult,
)
from thumbnail_api.s3 import build_output_key
from thumbnail_api.s3.operations import InputObject

_JOB_ID = "550e8400-e29b-41d4-a716-446655440000"
_INPUT_KEY = f"uploads/{_JOB_ID}/original"
_SIZE = 256
_OUTPUT_KEY = build_output_key(_JOB_ID, _SIZE)
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


def _png_bytes(width: int = 400, height: int = 300) -> bytes:
    image = Image.new("RGB", (width, height), color=(20, 40, 60))
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _job(
    *,
    status: JobStatus = "processing",
    size_status: SizeStatus = "pending",
) -> JobRecord:
    size_map: dict[str, SizeState] = {
        str(size): {"status": "pending", "output_key": None} for size in DEFAULT_THUMBNAIL_SIZES
    }
    size_map[str(_SIZE)] = {
        "status": size_status,
        "output_key": _OUTPUT_KEY if size_status == "complete" else None,
    }
    return JobRecord(
        job_id=_JOB_ID,
        status=status,
        input_key=_INPUT_KEY,
        sizes=size_map,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _sqs_event(
    body: object,
    *,
    receive_count: int = 1,
) -> dict[str, Any]:
    if not isinstance(body, str):
        body = json.dumps(body, separators=(",", ":"))
    return {
        "Records": [
            {
                "messageId": "msg-1",
                "body": body,
                "attributes": {"ApproximateReceiveCount": str(receive_count)},
                "eventSource": "aws:sqs",
            }
        ]
    }


def _valid_body(*, size: int = _SIZE) -> dict[str, object]:
    return {"job_id": _JOB_ID, "input_key": _INPUT_KEY, "size": size}


@pytest.fixture
def deps(monkeypatch: pytest.MonkeyPatch, config: Config) -> dict[str, Any]:
    jobs: dict[str, JobRecord] = {_JOB_ID: _job()}
    claims: list[tuple[str, int]] = []
    completes: list[tuple[str, int, str]] = []
    fails: list[tuple[str, int, str | None]] = []
    puts: list[dict[str, Any]] = []

    def fake_get(_client: object, _table: str, job_id: str) -> JobRecord | None:
        return jobs.get(job_id)

    def fake_claim(
        _client: object,
        _table: str,
        job_id: str,
        size: int | str,
    ) -> SizeUpdateResult:
        claims.append((job_id, int(size)))
        job = jobs[job_id]
        key = str(size)
        sizes = dict(job["sizes"])
        sizes[key] = {"status": "processing", "output_key": None}
        updated = JobRecord(
            job_id=job["job_id"],
            status=job["status"],
            input_key=job["input_key"],
            sizes=sizes,
            created_at=job["created_at"],
            updated_at=_NOW,
        )
        jobs[job_id] = updated
        return SizeUpdateResult(applied=True, job=updated)

    def fake_complete(
        _client: object,
        _table: str,
        job_id: str,
        size: int | str,
        output_key: str,
    ) -> SizeUpdateResult:
        completes.append((job_id, int(size), output_key))
        job = jobs[job_id]
        key = str(size)
        sizes = dict(job["sizes"])
        sizes[key] = {"status": "complete", "output_key": output_key}
        all_complete = all(entry["status"] == "complete" for entry in sizes.values())
        updated = JobRecord(
            job_id=job["job_id"],
            status="complete" if all_complete else job["status"],
            input_key=job["input_key"],
            sizes=sizes,
            created_at=job["created_at"],
            updated_at=_NOW,
        )
        jobs[job_id] = updated
        return SizeUpdateResult(applied=True, job=updated)

    def fake_fail(
        _client: object,
        _table: str,
        job_id: str,
        size: int | str,
        *,
        error: str | None = None,
    ) -> SizeUpdateResult:
        fails.append((job_id, int(size), error))
        job = jobs[job_id]
        key = str(size)
        sizes = dict(job["sizes"])
        entry: SizeState = {"status": "failed", "output_key": None}
        if error is not None:
            entry["error"] = error
        sizes[key] = entry
        updated = JobRecord(
            job_id=job["job_id"],
            status="failed",
            input_key=job["input_key"],
            sizes=sizes,
            created_at=job["created_at"],
            updated_at=_NOW,
        )
        jobs[job_id] = updated
        return SizeUpdateResult(applied=True, job=updated)

    def fake_get_input(
        _client: object,
        *,
        bucket: str,
        job_id: str,
    ) -> InputObject:
        assert bucket == config.input_bucket
        assert job_id == _JOB_ID
        return InputObject(key=_INPUT_KEY, body=_png_bytes(), content_type="image/png")

    def fake_put_output(
        _client: object,
        *,
        bucket: str,
        job_id: str,
        size: int,
        body: bytes,
    ) -> str:
        assert bucket == config.output_bucket
        assert body[:2] == b"\xff\xd8"
        key = build_output_key(job_id, size)
        puts.append({"job_id": job_id, "size": size, "key": key, "bytes": len(body)})
        return key

    monkeypatch.setattr(worker_module, "get_job", fake_get)
    monkeypatch.setattr(worker_module, "claim_size", fake_claim)
    monkeypatch.setattr(worker_module, "complete_size", fake_complete)
    monkeypatch.setattr(worker_module, "fail_size", fake_fail)
    monkeypatch.setattr(worker_module, "get_input_object", fake_get_input)
    monkeypatch.setattr(worker_module, "put_output_object", fake_put_output)

    return {
        "config": config,
        "s3_client": MagicMock(name="s3"),
        "dynamodb_client": MagicMock(name="dynamodb"),
        "jobs": jobs,
        "claims": claims,
        "completes": completes,
        "fails": fails,
        "puts": puts,
    }


def test_parse_work_message_accepts_valid_body() -> None:
    parsed = parse_work_message(
        json.dumps(_valid_body()),
        configured_sizes=DEFAULT_THUMBNAIL_SIZES,
    )
    assert parsed.job_id == _JOB_ID
    assert parsed.input_key == _INPUT_KEY
    assert parsed.size == _SIZE


@pytest.mark.parametrize(
    "body",
    [
        "{not-json",
        "[]",
        json.dumps({"input_key": _INPUT_KEY, "size": _SIZE}),
        json.dumps({"job_id": "", "input_key": _INPUT_KEY, "size": _SIZE}),
        json.dumps({"job_id": "a/b", "input_key": "uploads/a/b/original", "size": _SIZE}),
        json.dumps({"job_id": _JOB_ID, "input_key": "wrong", "size": _SIZE}),
        json.dumps({"job_id": _JOB_ID, "input_key": _INPUT_KEY, "size": "256"}),
        json.dumps({"job_id": _JOB_ID, "input_key": _INPUT_KEY, "size": 64}),
        json.dumps({"job_id": _JOB_ID, "input_key": _INPUT_KEY, "size": True}),
    ],
)
def test_parse_work_message_rejects_malformed(body: object) -> None:
    with pytest.raises(MalformedMessageError):
        parse_work_message(body, configured_sizes=DEFAULT_THUMBNAIL_SIZES)


def test_success_writes_output_and_completes_size(deps: dict[str, Any]) -> None:
    result = handle_worker(
        _sqs_event(_valid_body()),
        config=deps["config"],
        s3_client=deps["s3_client"],
        dynamodb_client=deps["dynamodb_client"],
    )

    assert result == {"ok": True}
    assert deps["claims"] == [(_JOB_ID, _SIZE)]
    assert len(deps["puts"]) == 1
    assert deps["puts"][0]["job_id"] == _JOB_ID
    assert deps["puts"][0]["size"] == _SIZE
    assert deps["puts"][0]["key"] == _OUTPUT_KEY
    assert deps["puts"][0]["bytes"] > 0
    assert deps["completes"] == [(_JOB_ID, _SIZE, _OUTPUT_KEY)]
    assert deps["fails"] == []
    assert deps["jobs"][_JOB_ID]["sizes"][str(_SIZE)]["status"] == "complete"
    assert deps["jobs"][_JOB_ID]["sizes"][str(_SIZE)]["output_key"] == _OUTPUT_KEY


def test_resize_failure_marks_size_failed_and_acks(
    deps: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_bytes: bytes, _max_size: int) -> bytes:
        msg = "input is not a recognized image"
        raise ImageProcessingError(msg)

    monkeypatch.setattr(worker_module, "resize_to_jpeg", boom)

    result = handle_worker(
        _sqs_event(_valid_body()),
        config=deps["config"],
        s3_client=deps["s3_client"],
        dynamodb_client=deps["dynamodb_client"],
    )

    assert result == {"ok": True}
    assert deps["completes"] == []
    assert len(deps["fails"]) == 1
    assert deps["fails"][0][0] == _JOB_ID
    assert deps["fails"][0][1] == _SIZE
    assert deps["jobs"][_JOB_ID]["status"] == "failed"
    assert deps["jobs"][_JOB_ID]["sizes"][str(_SIZE)]["status"] == "failed"


def test_missing_input_marks_failed(
    deps: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_client: object, **_kwargs: object) -> InputObject:
        raise ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "not found"}},
            "GetObject",
        )

    monkeypatch.setattr(worker_module, "get_input_object", missing)

    result = handle_worker(
        _sqs_event(_valid_body()),
        config=deps["config"],
        s3_client=deps["s3_client"],
        dynamodb_client=deps["dynamodb_client"],
    )

    assert result == {"ok": True}
    assert deps["fails"]
    assert deps["completes"] == []


def test_transient_error_retries_without_fail(
    deps: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def throttled(_client: object, **_kwargs: object) -> InputObject:
        raise ClientError(
            {"Error": {"Code": "SlowDown", "Message": "slow"}},
            "GetObject",
        )

    monkeypatch.setattr(worker_module, "get_input_object", throttled)

    with pytest.raises(ClientError, match="SlowDown"):
        handle_worker(
            _sqs_event(_valid_body(), receive_count=2),
            config=deps["config"],
            s3_client=deps["s3_client"],
            dynamodb_client=deps["dynamodb_client"],
        )

    assert deps["fails"] == []
    assert deps["completes"] == []


def test_exhausted_retries_marks_failed_then_raises(
    deps: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def throttled(_client: object, **_kwargs: object) -> InputObject:
        raise ClientError(
            {"Error": {"Code": "SlowDown", "Message": "slow"}},
            "GetObject",
        )

    monkeypatch.setattr(worker_module, "get_input_object", throttled)

    with pytest.raises(ClientError, match="SlowDown"):
        handle_worker(
            _sqs_event(_valid_body(), receive_count=DEFAULT_MAX_RECEIVE_COUNT),
            config=deps["config"],
            s3_client=deps["s3_client"],
            dynamodb_client=deps["dynamodb_client"],
        )

    assert deps["fails"]
    assert deps["jobs"][_JOB_ID]["sizes"][str(_SIZE)]["status"] == "failed"


def test_terminal_size_is_noop(deps: dict[str, Any]) -> None:
    deps["jobs"][_JOB_ID] = _job(size_status="complete")

    result = handle_worker(
        _sqs_event(_valid_body()),
        config=deps["config"],
        s3_client=deps["s3_client"],
        dynamodb_client=deps["dynamodb_client"],
    )

    assert result == {"ok": True}
    assert deps["claims"] == []
    assert deps["puts"] == []
    assert deps["completes"] == []


def test_malformed_message_fails_invocation_without_dynamodb(deps: dict[str, Any]) -> None:
    with pytest.raises(MalformedMessageError):
        handle_worker(
            _sqs_event("{bad"),
            config=deps["config"],
            s3_client=deps["s3_client"],
            dynamodb_client=deps["dynamodb_client"],
        )

    assert deps["claims"] == []
    assert deps["fails"] == []


def test_missing_job_raises(deps: dict[str, Any]) -> None:
    deps["jobs"].clear()

    with pytest.raises(JobNotFoundError, match="job not found"):
        handle_worker(
            _sqs_event(_valid_body()),
            config=deps["config"],
            s3_client=deps["s3_client"],
            dynamodb_client=deps["dynamodb_client"],
        )


def test_handler_wires_config_and_clients(
    monkeypatch: pytest.MonkeyPatch,
    config: Config,
) -> None:
    s3 = MagicMock(name="s3")
    dynamodb = MagicMock(name="dynamodb")

    def fake_get_config(*, env_file: object = None) -> Config:
        del env_file
        return config

    def fake_get_s3_client(_config: Config) -> MagicMock:
        return s3

    def fake_get_dynamodb_client(_config: Config) -> MagicMock:
        return dynamodb

    monkeypatch.setattr(worker_module, "get_config", fake_get_config)
    monkeypatch.setattr(worker_module, "get_s3_client", fake_get_s3_client)
    monkeypatch.setattr(worker_module, "get_dynamodb_client", fake_get_dynamodb_client)

    called: dict[str, Any] = {}

    def fake_handle(
        event: dict[str, Any],
        *,
        config: Config,
        s3_client: object,
        dynamodb_client: object,
        max_receive_count: int = DEFAULT_MAX_RECEIVE_COUNT,
    ) -> dict[str, Any]:
        called["event"] = event
        called["config"] = config
        called["s3_client"] = s3_client
        called["dynamodb_client"] = dynamodb_client
        called["max_receive_count"] = max_receive_count
        return {"ok": True}

    monkeypatch.setattr(worker_module, "handle_worker", fake_handle)

    event = _sqs_event(_valid_body())
    assert handler(event, None) == {"ok": True}
    assert called["event"] is event
    assert called["config"] is config
    assert called["s3_client"] is s3
    assert called["dynamodb_client"] is dynamodb
