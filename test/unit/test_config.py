import pytest

from thumbnail_api.config.types import Config
from thumbnail_api.main import run


def test_run_uses_injected_config(capsys: pytest.CaptureFixture[str]) -> None:
    config = Config(
        environment="test",
    )

    run(config)

    captured = capsys.readouterr()
    assert "Hello from thumbnail-api! The environment is test." in captured.out
