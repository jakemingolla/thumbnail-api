import pytest

from python_template.config.types import Config
from python_template.main import run


def test_run_uses_injected_config(capsys: pytest.CaptureFixture[str]) -> None:
    config = Config(
        environment="test",
    )

    run(config)

    captured = capsys.readouterr()
    assert "Hello from python-template! The environment is test." in captured.out
