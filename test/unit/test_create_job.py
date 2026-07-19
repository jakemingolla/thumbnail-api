"""Unit tests for the create_job API Gateway handler."""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from thumbnail_api.config.types import DEFAULT_THUMBNAIL_SIZES, Config
from thumbnail_api.handlers import create_job as create_job_module
from thumbnail_api.handlers.create_job import handle_create_job, handler
from thumbnail_api.jobs.types import JobRecord, SizeState
from thumbnail_api.s3 import PresignedUpload

_JOB_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_INPUT_KEY = f"uploads/{_JOB_ID}/original"
_UPLOAD_URL = (
    f"http://127.0.0.1:4566/thumbnail-input/{_INPUT_KEY}"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=test"
)
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


@pytest.fixture
def deps(
    monkeypatch: pytest.MonkeyPatch,
    config: Config,
) -> dict[str, Any]:
    """Wire create_job seams: fixed job id, mocked DynamoDB put + S3 presign."""
    monkeypatch.setattr(create_job_module, "_new_job_id", lambda: _JOB_ID)

    put_calls: list[dict[str, Any]] = []
    presign_calls: list[dict[str, Any]] = []

    def fake_put(
        _client: object,
        table_name: str,
        *,
        job_id: str,
        input_key: str,
        sizes: list[int],
    ) -> JobRecord:
        put_calls.append(
            {
                "table_name": table_name,
                "job_id": job_id,
                "input_key": input_key,
                "sizes": list(sizes),
            }
        )
        size_map: dict[str, SizeState] = {
            str(size): {"status": "pending", "output_key": None} for size in sizes
        }
        return JobRecord(
            job_id=job_id,
            status="pending",
            input_key=input_key,
            sizes=size_map,
            created_at=_NOW,
            updated_at=_NOW,
        )

    def fake_presign(
        _client: object,
        *,
        bucket: str,
        job_id: str,
        content_type: str,
        expires_in: int = 3600,
    ) -> PresignedUpload:
        presign_calls.append(
            {
                "bucket": bucket,
                "job_id": job_id,
                "content_type": content_type,
                "expires_in": expires_in,
            }
        )
        return PresignedUpload(url=_UPLOAD_URL, key=f"uploads/{job_id}/original")

    monkeypatch.setattr(create_job_module, "put_pending_job", fake_put)
    monkeypatch.setattr(create_job_module, "generate_presigned_put_url", fake_presign)

    return {
        "config": config,
        "s3_client": MagicMock(name="s3"),
        "dynamodb_client": MagicMock(name="dynamodb"),
        "put_calls": put_calls,
        "presign_calls": presign_calls,
    }


def _event(
    *,
    body: str | dict[str, Any] | None = None,
    content_type: str | None = "application/json",
    headers: dict[str, str] | None = None,
    is_base64: bool = False,
) -> dict[str, Any]:
    if headers is None:
        headers = {}
        if content_type is not None:
            headers["Content-Type"] = content_type
    if isinstance(body, dict):
        raw = json.dumps(body)
    elif body is None:
        raw = json.dumps({"content_type": "image/jpeg"})
    else:
        raw = body
    if is_base64:
        raw = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    return {
        "httpMethod": "POST",
        "path": "/jobs",
        "headers": headers,
        "body": raw,
        "isBase64Encoded": is_base64,
    }


def _body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])


def test_create_job_success(deps: dict[str, Any]) -> None:
    response = handle_create_job(
        _event(body={"content_type": "image/png"}),
        config=deps["config"],
        s3_client=deps["s3_client"],
        dynamodb_client=deps["dynamodb_client"],
    )

    assert response["statusCode"] == 201
    assert response["headers"]["Content-Type"] == "application/json"
    assert _body(response) == {
        "job_id": _JOB_ID,
        "upload_url": _UPLOAD_URL,
        "input_key": _INPUT_KEY,
        "status": "pending",
    }
    assert deps["put_calls"] == [
        {
            "table_name": "thumbnail-jobs",
            "job_id": _JOB_ID,
            "input_key": _INPUT_KEY,
            "sizes": list(DEFAULT_THUMBNAIL_SIZES),
        }
    ]
    assert deps["presign_calls"] == [
        {
            "bucket": "thumbnail-input",
            "job_id": _JOB_ID,
            "content_type": "image/png",
            "expires_in": 3600,
        }
    ]


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "application/json; charset=utf-8",
        "Application/JSON",
    ],
)
def test_accepts_json_content_type_variants(
    deps: dict[str, Any],
    content_type: str,
) -> None:
    response = handle_create_job(
        _event(content_type=content_type),
        config=deps["config"],
        s3_client=deps["s3_client"],
        dynamodb_client=deps["dynamodb_client"],
    )
    assert response["statusCode"] == 201


def test_accepts_lowercase_content_type_header(deps: dict[str, Any]) -> None:
    response = handle_create_job(
        _event(headers={"content-type": "application/json"}),
        config=deps["config"],
        s3_client=deps["s3_client"],
        dynamodb_client=deps["dynamodb_client"],
    )
    assert response["statusCode"] == 201


def test_accepts_base64_encoded_body(deps: dict[str, Any]) -> None:
    response = handle_create_job(
        _event(body={"content_type": "image/webp"}, is_base64=True),
        config=deps["config"],
        s3_client=deps["s3_client"],
        dynamodb_client=deps["dynamodb_client"],
    )
    assert response["statusCode"] == 201
    assert deps["presign_calls"][0]["content_type"] == "image/webp"


@pytest.mark.parametrize(
    ("event", "status", "code"),
    [
        (_event(content_type=None), 415, "unsupported_media_type"),
        (_event(content_type="text/plain"), 415, "unsupported_media_type"),
        (_event(body="not-json"), 400, "invalid_json"),
        (_event(body=""), 400, "invalid_json"),
        (_event(body="[]"), 400, "invalid_request"),
        (_event(body="null"), 400, "invalid_request"),
        (_event(body='"x"'), 400, "invalid_request"),
        (_event(body={}), 400, "invalid_request"),
        (_event(body={"content_type": "image/gif"}), 400, "unsupported_content_type"),
        (
            _event(body={"content_type": "image/jpeg", "extra": 1}),
            400,
            "invalid_request",
        ),
        (_event(body={"content_type": 1}), 400, "invalid_request"),
    ],
)
def test_validation_failures(
    deps: dict[str, Any],
    event: dict[str, Any],
    status: int,
    code: str,
) -> None:
    response = handle_create_job(
        event,
        config=deps["config"],
        s3_client=deps["s3_client"],
        dynamodb_client=deps["dynamodb_client"],
    )

    assert response["statusCode"] == status
    body = _body(response)
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)
    assert body["error"]["message"]
    assert deps["put_calls"] == []
    assert deps["presign_calls"] == []


def test_internal_error_when_put_fails(
    deps: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_args: object, **_kwargs: object) -> JobRecord:
        msg = "dynamodb down"
        raise RuntimeError(msg)

    monkeypatch.setattr(create_job_module, "put_pending_job", boom)

    response = handle_create_job(
        _event(),
        config=deps["config"],
        s3_client=deps["s3_client"],
        dynamodb_client=deps["dynamodb_client"],
    )

    assert response["statusCode"] == 500
    assert _body(response)["error"]["code"] == "internal_error"


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

    monkeypatch.setattr(create_job_module, "get_config", fake_get_config)
    monkeypatch.setattr(create_job_module, "get_s3_client", fake_get_s3_client)
    monkeypatch.setattr(create_job_module, "get_dynamodb_client", fake_get_dynamodb_client)
    monkeypatch.setattr(create_job_module, "_new_job_id", lambda: _JOB_ID)

    sizes: dict[str, SizeState] = {"128": {"status": "pending", "output_key": None}}
    put = MagicMock(
        return_value=JobRecord(
            job_id=_JOB_ID,
            status="pending",
            input_key=_INPUT_KEY,
            sizes=sizes,
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    presign = MagicMock(return_value=PresignedUpload(url=_UPLOAD_URL, key=_INPUT_KEY))
    monkeypatch.setattr(create_job_module, "put_pending_job", put)
    monkeypatch.setattr(create_job_module, "generate_presigned_put_url", presign)

    response = handler(_event(), None)

    assert response["statusCode"] == 201
    put.assert_called_once()
    assert put.call_args.args[0] is dynamodb
    presign.assert_called_once()
    assert presign.call_args.args[0] is s3
