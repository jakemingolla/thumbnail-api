from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest

from thumbnail_api.config.clients import get_s3_client
from thumbnail_api.config.types import Config
from thumbnail_api.s3 import (
    ALLOWED_UPLOAD_CONTENT_TYPES,
    OUTPUT_CONTENT_TYPE,
    build_input_key,
    build_output_key,
    generate_presigned_put_url,
    get_input_object,
    parse_input_key,
    put_output_object,
    validate_upload_content_type,
)

_JOB_ID = "550e8400-e29b-41d4-a716-446655440000"
_LOCALSTACK_ENDPOINT = "http://127.0.0.1:4566"


@pytest.fixture
def aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def s3_config() -> Config:
    return Config(
        environment="test",
        input_bucket="thumbnail-input",
        output_bucket="thumbnail-output",
        jobs_table="thumbnail-jobs",
        queue_url="http://127.0.0.1:4566/000000000000/thumbnail-work",
        aws_endpoint_url=_LOCALSTACK_ENDPOINT,
    )


def test_build_input_key() -> None:
    assert build_input_key(_JOB_ID) == f"uploads/{_JOB_ID}/original"


def test_parse_input_key() -> None:
    assert parse_input_key(f"uploads/{_JOB_ID}/original") == _JOB_ID


@pytest.mark.parametrize(
    "key",
    [
        "",
        "uploads//original",
        "uploads/has/slash/original",
        "thumbnails/x/128.jpg",
        f"uploads/{_JOB_ID}/ORIGINAL",
    ],
)
def test_parse_input_key_rejects_invalid(key: str) -> None:
    with pytest.raises(ValueError, match=r"key must match|job_id"):
        parse_input_key(key)


def test_build_output_key() -> None:
    assert build_output_key(_JOB_ID, 256) == f"thumbnails/{_JOB_ID}/256.jpg"


@pytest.mark.parametrize(
    "job_id",
    ["", "has/slash", "a//b"],
)
def test_build_input_key_rejects_invalid_job_id(job_id: str) -> None:
    with pytest.raises(ValueError, match="job_id"):
        build_input_key(job_id)


@pytest.mark.parametrize("size", [0, -1])
def test_build_output_key_rejects_invalid_size(size: int) -> None:
    with pytest.raises(ValueError, match="size"):
        build_output_key(_JOB_ID, size)


def test_validate_upload_content_type() -> None:
    for content_type in sorted(ALLOWED_UPLOAD_CONTENT_TYPES):
        assert validate_upload_content_type(content_type) == content_type

    with pytest.raises(ValueError, match="content_type"):
        validate_upload_content_type("image/gif")


@pytest.mark.usefixtures("aws_credentials")
def test_presigned_put_url_points_at_localstack_endpoint(s3_config: Config) -> None:
    s3 = get_s3_client(s3_config)

    upload = generate_presigned_put_url(
        s3,
        bucket=s3_config.input_bucket,
        job_id=_JOB_ID,
        content_type="image/jpeg",
    )

    parsed = urlparse(upload.url)
    assert upload.key == f"uploads/{_JOB_ID}/original"
    assert parsed.scheme == "http"
    assert parsed.netloc == "127.0.0.1:4566"
    assert parsed.path == f"/{s3_config.input_bucket}/{upload.key}"
    assert "amazonaws.com" not in upload.url
    assert f"{s3_config.input_bucket}.s3" not in upload.url

    query = parse_qs(parsed.query)
    assert query.get("X-Amz-Algorithm") == ["AWS4-HMAC-SHA256"]
    assert "X-Amz-Signature" in query


@pytest.mark.usefixtures("aws_credentials")
def test_presigned_put_url_rejects_bad_content_type(s3_config: Config) -> None:
    s3 = get_s3_client(s3_config)

    with pytest.raises(ValueError, match="content_type"):
        generate_presigned_put_url(
            s3,
            bucket=s3_config.input_bucket,
            job_id=_JOB_ID,
            content_type="text/plain",
        )


def test_get_input_object() -> None:
    body = MagicMock()
    body.read.return_value = b"image-bytes"
    s3 = MagicMock()
    s3.get_object.return_value = {
        "Body": body,
        "ContentType": "image/png",
    }

    result = get_input_object(s3, bucket="thumbnail-input", job_id=_JOB_ID)

    s3.get_object.assert_called_once_with(
        Bucket="thumbnail-input",
        Key=f"uploads/{_JOB_ID}/original",
    )
    assert result.key == f"uploads/{_JOB_ID}/original"
    assert result.body == b"image-bytes"
    assert result.content_type == "image/png"


def test_put_output_object() -> None:
    s3 = MagicMock()

    key = put_output_object(
        s3,
        bucket="thumbnail-output",
        job_id=_JOB_ID,
        size=128,
        body=b"jpeg-bytes",
    )

    assert key == f"thumbnails/{_JOB_ID}/128.jpg"
    s3.put_object.assert_called_once_with(
        Bucket="thumbnail-output",
        Key=key,
        Body=b"jpeg-bytes",
        ContentType=OUTPUT_CONTENT_TYPE,
    )
