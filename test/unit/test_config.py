import pytest
from pydantic import ValidationError

from thumbnail_api.config.clients import (
    get_dynamodb_client,
    get_s3_client,
    get_sqs_client,
)
from thumbnail_api.config.main import get_config
from thumbnail_api.config.types import DEFAULT_THUMBNAIL_SIZES, Config
from thumbnail_api.main import run

_REQUIRED_ENV = {
    "ENVIRONMENT": "test",
    "INPUT_BUCKET": "input-bucket",
    "OUTPUT_BUCKET": "output-bucket",
    "JOBS_TABLE": "jobs",
    "QUEUE_URL": "http://127.0.0.1:4566/000000000000/thumbnail-work",
    "AWS_ENDPOINT_URL": "http://127.0.0.1:4566",
}


@pytest.fixture
def required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)


def test_run_uses_injected_config(capsys: pytest.CaptureFixture[str]) -> None:
    config = Config(
        environment="test",
        input_bucket="input-bucket",
        output_bucket="output-bucket",
        jobs_table="jobs",
        queue_url="http://127.0.0.1:4566/000000000000/thumbnail-work",
        aws_endpoint_url="http://127.0.0.1:4566",
    )

    run(config)

    captured = capsys.readouterr()
    assert "Hello from thumbnail-api! The environment is test." in captured.out


@pytest.mark.usefixtures("required_env")
def test_get_config_loads_required_env() -> None:
    config = get_config(env_file=None)

    assert config.environment == "test"
    assert config.input_bucket == "input-bucket"
    assert config.output_bucket == "output-bucket"
    assert config.jobs_table == "jobs"
    assert config.queue_url == _REQUIRED_ENV["QUEUE_URL"]
    assert config.aws_endpoint_url == "http://127.0.0.1:4566"
    assert config.aws_region == "us-east-1"
    assert config.thumbnail_sizes == DEFAULT_THUMBNAIL_SIZES


@pytest.mark.usefixtures("required_env")
def test_get_config_parses_thumbnail_sizes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THUMBNAIL_SIZES", "64, 128, 256")

    config = get_config(env_file=None)

    assert config.thumbnail_sizes == [64, 128, 256]


@pytest.mark.parametrize(
    "missing_key",
    [
        "INPUT_BUCKET",
        "OUTPUT_BUCKET",
        "JOBS_TABLE",
        "QUEUE_URL",
        "AWS_ENDPOINT_URL",
        "ENVIRONMENT",
    ],
)
def test_get_config_fails_when_required_env_missing(
    missing_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in _REQUIRED_ENV.items():
        if key == missing_key:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)

    with pytest.raises(ValidationError):
        get_config(env_file=None)


@pytest.mark.usefixtures("required_env")
@pytest.mark.parametrize(
    ("env_key", "env_value"),
    [
        ("INPUT_BUCKET", ""),
        ("OUTPUT_BUCKET", ""),
        ("JOBS_TABLE", ""),
        ("QUEUE_URL", ""),
        ("AWS_ENDPOINT_URL", ""),
        ("THUMBNAIL_SIZES", ""),
        ("THUMBNAIL_SIZES", "128,abc"),
        ("THUMBNAIL_SIZES", "0,128"),
        ("THUMBNAIL_SIZES", "-1"),
    ],
)
def test_get_config_fails_on_invalid_env(
    env_key: str,
    env_value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(env_key, env_value)

    with pytest.raises(ValidationError):
        get_config(env_file=None)


@pytest.mark.usefixtures("required_env")
def test_clients_use_endpoint_and_path_style_s3() -> None:
    config = get_config(env_file=None)

    s3 = get_s3_client(config)
    dynamodb = get_dynamodb_client(config)
    sqs = get_sqs_client(config)

    assert s3.meta.endpoint_url == config.aws_endpoint_url
    assert dynamodb.meta.endpoint_url == config.aws_endpoint_url
    assert sqs.meta.endpoint_url == config.aws_endpoint_url
    assert s3.meta.config.s3["addressing_style"] == "path"
    assert s3.meta.config.signature_version == "s3v4"
