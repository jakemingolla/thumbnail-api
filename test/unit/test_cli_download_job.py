"""Unit tests for ``python -m thumbnail_api.cli download-job`` (mocked HTTP/S3)."""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from thumbnail_api.cli import download_job as download_job_module
from thumbnail_api.cli.download_job import main

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


_JOB_ID = "job-123"
_API_BASE = "http://api.example/dev"
_ENDPOINT = "http://127.0.0.1:4566"
_BUCKET = "thumbnail-output"
_LOCALSTACK_ACCESS_KEY = "akid"
_DUMMY_CRED = "dummy-token"


def _resolve_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_api_base(_explicit: str | None) -> str:
        return _API_BASE

    def fake_endpoint(_explicit: str | None) -> str:
        return _ENDPOINT

    def fake_bucket(_explicit: str | None) -> str:
        return _BUCKET

    monkeypatch.setattr(download_job_module, "resolve_api_base", fake_api_base)
    monkeypatch.setattr(download_job_module, "resolve_localstack_endpoint", fake_endpoint)
    monkeypatch.setattr(download_job_module, "resolve_output_bucket", fake_bucket)


def _stub_http(monkeypatch: pytest.MonkeyPatch, status: int, payload: dict[str, Any]) -> None:
    monkeypatch.setattr(
        download_job_module,
        "http_json",
        MagicMock(return_value=(status, payload)),
    )


def _stub_s3(monkeypatch: pytest.MonkeyPatch, s3: object) -> None:
    def fake_s3(_endpoint: str) -> object:
        return s3

    monkeypatch.setattr(download_job_module, "_s3_client", fake_s3)


def _job(*, status: str = "complete", sizes: Mapping[str, object] | None = None) -> dict[str, Any]:
    if sizes is None:
        sizes = {
            "512": {"status": "complete", "output_key": "thumbnails/job-123/512.jpg"},
            "256": {"status": "complete", "output_key": "thumbnails/job-123/256.jpg"},
            "1024": {"status": "pending"},
        }
    return {"job_id": _JOB_ID, "status": status, "sizes": sizes}


class _FakeS3:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.calls: list[tuple[str, str]] = []

    def get_object(self, **kwargs: object) -> dict[str, Any]:
        bucket = str(kwargs["Bucket"])
        key = str(kwargs["Key"])
        self.calls.append((bucket, key))
        body = self.objects.get(key)
        if body is None:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}},
                "GetObject",
            )
        return {"Body": BytesIO(body)}


class _StaticBodyS3:
    def __init__(self, body: object) -> None:
        self.body = body

    def get_object(self, **_kwargs: object) -> dict[str, Any]:
        return {"Body": self.body}


def test_main_rejects_empty_job_id(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["   "]) == 1
    assert "job_id must be non-empty" in capsys.readouterr().err


def test_main_rejects_http_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _resolve_ok(monkeypatch)
    _stub_http(monkeypatch, 404, {"error": "not found"})
    assert main([_JOB_ID]) == 1
    err = capsys.readouterr().err
    assert "GET /jobs/job-123 returned HTTP 404" in err
    assert "not found" in err


def test_main_rejects_sizes_not_object(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _resolve_ok(monkeypatch)
    _stub_http(monkeypatch, 200, {"job_id": _JOB_ID, "sizes": ["256"]})
    assert main([_JOB_ID]) == 1
    assert "job.sizes must be an object" in capsys.readouterr().err


@pytest.mark.parametrize(
    "entry",
    [
        {"status": "complete"},
        {"status": "complete", "output_key": ""},
        {"status": "complete", "output_key": 1},
    ],
)
def test_main_rejects_complete_without_output_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entry: dict[str, object],
) -> None:
    _resolve_ok(monkeypatch)
    _stub_http(monkeypatch, 200, _job(sizes={"256": entry}))
    assert main([_JOB_ID]) == 1
    assert "missing output_key" in capsys.readouterr().err


def test_main_rejects_no_complete_sizes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _resolve_ok(monkeypatch)
    _stub_http(
        monkeypatch,
        200,
        _job(status="pending", sizes={"256": {"status": "pending"}, "bad": "not-a-map"}),
    )
    assert main([_JOB_ID]) == 1
    assert "no complete sizes with output_key" in capsys.readouterr().err


def test_main_wraps_s3_client_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _resolve_ok(monkeypatch)
    _stub_http(
        monkeypatch, 200, _job(sizes={"256": {"status": "complete", "output_key": "missing.jpg"}})
    )
    _stub_s3(monkeypatch, _FakeS3({}))
    assert main([_JOB_ID, "--out-dir", str(tmp_path)]) == 1
    assert "S3 get_object failed" in capsys.readouterr().err


def test_main_rejects_unexpected_s3_body(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _resolve_ok(monkeypatch)
    _stub_http(monkeypatch, 200, _job(sizes={"256": {"status": "complete", "output_key": "k256"}}))
    _stub_s3(monkeypatch, _StaticBodyS3(object()))
    assert main([_JOB_ID, "--out-dir", str(tmp_path)]) == 1
    assert "unexpected S3 body" in capsys.readouterr().err


def test_main_rejects_non_bytes_s3_body(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class TextBody:
        def read(self) -> str:
            return "not-bytes"

    _resolve_ok(monkeypatch)
    _stub_http(monkeypatch, 200, _job(sizes={"256": {"status": "complete", "output_key": "k256"}}))
    _stub_s3(monkeypatch, _StaticBodyS3(TextBody()))
    assert main([_JOB_ID, "--out-dir", str(tmp_path)]) == 1
    assert "unexpected S3 body type" in capsys.readouterr().err


def test_main_downloads_complete_sizes_and_lists_skipped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _resolve_ok(monkeypatch)
    _stub_http(
        monkeypatch,
        200,
        _job(
            sizes={
                "512": {"status": "complete", "output_key": "thumbnails/job-123/512.jpg"},
                "256": {"status": "complete", "output_key": "thumbnails/job-123/256.jpg"},
                "1024": {"status": "pending"},
                "2048": {"status": "failed"},
                "custom": "not-a-map",
                "weird": {"status": 3},
                "none": {"status": None},
            }
        ),
    )
    s3 = _FakeS3(
        {
            "thumbnails/job-123/256.jpg": b"small",
            "thumbnails/job-123/512.jpg": b"medium-bytes",
        }
    )
    _stub_s3(monkeypatch, s3)

    out_dir = tmp_path / "nested" / "thumbs"
    code = main([_JOB_ID, "--out-dir", str(out_dir)])
    captured = capsys.readouterr()

    assert code == 0
    assert (out_dir / "256.jpg").read_bytes() == b"small"
    assert (out_dir / "512.jpg").read_bytes() == b"medium-bytes"
    assert not (out_dir / "1024.jpg").exists()
    assert s3.calls == [
        (_BUCKET, "thumbnails/job-123/256.jpg"),
        (_BUCKET, "thumbnails/job-123/512.jpg"),
    ]
    assert "download-job" in captured.out
    assert "wrote 2 file(s)" in captured.out
    assert "skipped" in captured.out
    assert "5 non-complete size(s)" in captured.out
    assert "pending" in captured.out
    assert "failed" in captured.out


def test_main_success_without_skipped_passes_explicit_flags(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: dict[str, str | None] = {}

    def fake_api_base(explicit: str | None) -> str:
        seen["api"] = explicit
        return _API_BASE

    def fake_endpoint(explicit: str | None) -> str:
        seen["endpoint"] = explicit
        return _ENDPOINT

    def fake_bucket(explicit: str | None) -> str:
        seen["bucket"] = explicit
        return "custom-bucket"

    monkeypatch.setattr(download_job_module, "resolve_api_base", fake_api_base)
    monkeypatch.setattr(download_job_module, "resolve_localstack_endpoint", fake_endpoint)
    monkeypatch.setattr(download_job_module, "resolve_output_bucket", fake_bucket)
    _stub_http(monkeypatch, 200, _job(sizes={"256": {"status": "complete", "output_key": "k256"}}))
    s3 = _FakeS3({"k256": b"ok"})
    _stub_s3(monkeypatch, s3)

    code = main(
        [
            _JOB_ID,
            "--out-dir",
            str(tmp_path),
            "--api-base",
            "http://custom.example/dev",
            "--localstack-endpoint",
            "http://ls:4566",
            "--output-bucket",
            "custom-bucket",
        ]
    )
    out = capsys.readouterr().out

    assert code == 0
    assert seen == {
        "api": "http://custom.example/dev",
        "endpoint": "http://ls:4566",
        "bucket": "custom-bucket",
    }
    assert s3.calls == [("custom-bucket", "k256")]
    assert "wrote 1 file(s)" in out
    assert "skipped" not in out
    assert (tmp_path / "256.jpg").read_bytes() == b"ok"


@pytest.mark.parametrize(
    ("env", "expected_region"),
    [
        ({"AWS_REGION": "eu-west-1", "AWS_DEFAULT_REGION": "us-west-2"}, "eu-west-1"),
        ({"AWS_DEFAULT_REGION": "ap-southeast-2"}, "ap-southeast-2"),
        ({}, "us-east-1"),
    ],
)
def test_main_builds_s3_client_from_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env: dict[str, str],
    expected_region: str,
) -> None:
    _resolve_ok(monkeypatch)
    _stub_http(monkeypatch, 200, _job(sizes={"256": {"status": "complete", "output_key": "k256"}}))
    s3 = _FakeS3({"k256": b"ok"})
    created = MagicMock(return_value=s3)
    monkeypatch.setattr(download_job_module.boto3, "client", created)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", _LOCALSTACK_ACCESS_KEY)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", _DUMMY_CRED)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    assert main([_JOB_ID, "--out-dir", str(tmp_path)]) == 0
    created.assert_called_once()
    kwargs = created.call_args.kwargs
    assert created.call_args.args == ("s3",)
    assert kwargs["endpoint_url"] == _ENDPOINT
    assert kwargs["region_name"] == expected_region
    assert kwargs["aws_access_key_id"] == _LOCALSTACK_ACCESS_KEY
    assert kwargs["aws_secret_access_key"] == _DUMMY_CRED
    assert kwargs["config"] is not None
    assert (tmp_path / "256.jpg").read_bytes() == b"ok"
