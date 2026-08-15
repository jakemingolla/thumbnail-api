"""Unit tests for ``python -m thumbnail_api.cli upload-watch`` (mocked HTTP)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from thumbnail_api.cli import upload_watch as upload_watch_module
from thumbnail_api.cli.upload_watch import main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _resolve_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_api_base(_explicit: str | None) -> str:
        return "http://api.example/dev"

    def fake_endpoint(_explicit: str | None) -> str:
        return "http://127.0.0.1:4566"

    monkeypatch.setattr(upload_watch_module, "resolve_api_base", fake_api_base)
    monkeypatch.setattr(upload_watch_module, "resolve_localstack_endpoint", fake_endpoint)


def test_main_no_wait_skips_poll(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _resolve_ok(monkeypatch)
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"jpeg-bytes")

    monkeypatch.setattr(
        upload_watch_module,
        "http_json",
        MagicMock(
            return_value=(
                201,
                {
                    "job_id": "job-123",
                    "upload_url": "http://localhost.localstack.cloud:4566/b/k",
                    "input_key": "uploads/job-123/original",
                    "status": "pending",
                },
            )
        ),
    )
    monkeypatch.setattr(upload_watch_module, "http_put", MagicMock(return_value=200))
    poll = MagicMock()
    monkeypatch.setattr(upload_watch_module, "_poll_until_terminal", poll)

    code = main([str(image), "--no-wait"])
    out = capsys.readouterr().out

    assert code == 0
    poll.assert_not_called()
    assert "job-123" in out
    assert "admin-status --watch" in out
    assert "uploaded (not waiting)" in out


def test_main_no_wait_allows_negative_timeout_flags(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """--timeout/--interval are unused with --no-wait; do not validate them."""
    _resolve_ok(monkeypatch)
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"jpeg-bytes")
    monkeypatch.setattr(
        upload_watch_module,
        "http_json",
        MagicMock(
            return_value=(
                201,
                {
                    "job_id": "job-456",
                    "upload_url": "http://localhost.localstack.cloud:4566/b/k",
                    "input_key": "uploads/job-456/original",
                    "status": "pending",
                },
            )
        ),
    )
    monkeypatch.setattr(upload_watch_module, "http_put", MagicMock(return_value=200))
    monkeypatch.setattr(upload_watch_module, "_poll_until_terminal", MagicMock())

    code = main([str(image), "--no-wait", "--timeout", "-1", "--interval", "-1"])
    assert code == 0


def test_main_rejects_bad_timeout_when_waiting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _resolve_ok(monkeypatch)
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"jpeg-bytes")

    code = main([str(image), "--timeout", "-1"])
    err = capsys.readouterr().err
    assert code == 1
    assert "--timeout must be positive" in err
