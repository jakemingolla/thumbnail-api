import pytest
from pydantic import SecretStr

from python_template.config.types import Config
from python_template.main import run


def test_run_uses_injected_config(capsys: pytest.CaptureFixture[str]) -> None:
    config = Config(
        openai_api_key=SecretStr("test-key"),
        default_model="gpt-test",
    )

    run(config)

    captured = capsys.readouterr()
    assert "Hello from python-template!" in captured.out
    assert "gpt-test" in captured.out
