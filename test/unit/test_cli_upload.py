"""Unit tests for ``python -m thumbnail_api.cli upload`` (mocked HTTP)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from thumbnail_api.cli import upload as upload_module
from thumbnail_api.cli.upload import main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _resolve_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_api_base(_explicit: str | None) -> str:
        return "http://api.example/dev"

    def fake_endpoint(_explicit: str | None) -> str:
        return "http://127.0.0.1:4566"

    monkeypatch.setattr(upload_module, "resolve_api_base", fake_api_base)
    monkeypatch.setattr(upload_module, "resolve_localstack_endpoint", fake_endpoint)


def _stub_create_and_put(monkeypatch: pytest.MonkeyPatch, job_id: str) -> None:
    monkeypatch.setattr(
        upload_module,
        "http_json",
        MagicMock(
            return_value=(
                201,
                {
                    "job_id": job_id,
                    "upload_url": "http://localhost.localstack.cloud:4566/b/k",
                    "input_key": f"uploads/{job_id}/original",
                    "status": "pending",
                },
            )
        ),
    )
    monkeypatch.setattr(upload_module, "http_put", MagicMock(return_value=200))


def test_main_default_skips_poll(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _resolve_ok(monkeypatch)
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"jpeg-bytes")
    _stub_create_and_put(monkeypatch, "job-123")
    poll = MagicMock()
    monkeypatch.setattr(upload_module, "_poll_until_terminal", poll)

    code = main([str(image)])
    out = capsys.readouterr().out

    assert code == 0
    poll.assert_not_called()
    assert "job-123" in out
    assert "admin-status --watch" in out
    assert "uploaded" in out


def test_main_default_allows_negative_timeout_flags(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """--timeout/--interval are unused without --watch; do not validate them."""
    _resolve_ok(monkeypatch)
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"jpeg-bytes")
    _stub_create_and_put(monkeypatch, "job-456")
    monkeypatch.setattr(upload_module, "_poll_until_terminal", MagicMock())

    code = main([str(image), "--timeout", "-1", "--interval", "-1"])
    assert code == 0


def test_main_watch_polls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _resolve_ok(monkeypatch)
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"jpeg-bytes")
    _stub_create_and_put(monkeypatch, "job-789")
    poll = MagicMock(
        return_value={
            "job_id": "job-789",
            "status": "complete",
            "sizes": {"256": {"status": "complete"}},
        }
    )
    monkeypatch.setattr(upload_module, "_poll_until_terminal", poll)

    code = main([str(image), "--watch"])
    out = capsys.readouterr().out

    assert code == 0
    poll.assert_called_once()
    assert "job complete" in out
    assert "just download-job job-789" in out


def test_main_rejects_bad_timeout_when_watching(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _resolve_ok(monkeypatch)
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"jpeg-bytes")

    code = main([str(image), "--watch", "--timeout", "-1"])
    err = capsys.readouterr().err
    assert code == 1
    assert "--timeout must be positive" in err
