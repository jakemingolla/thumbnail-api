"""Unit tests for ``python -m thumbnail_api.cli bench`` (mocked HTTP, no Docker)."""

from __future__ import annotations

from dataclasses import replace
from io import BytesIO
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from PIL import Image

from thumbnail_api.cli import bench as bench_module
from thumbnail_api.cli.bench import (
    BenchRun,
    BenchSettings,
    format_table,
    metric_stats,
    parse_args,
    report_payload,
    run_once,
    settings_from_args,
    synthetic_jpeg_bytes,
)
from thumbnail_api.cli.local import CliError

if TYPE_CHECKING:
    from argparse import Namespace
    from pathlib import Path


def _settings(
    *,
    label: str | None = None,
    runs: int = 5,
    warmup: int = 1,
    json_out: bool = False,
) -> BenchSettings:
    return replace(
        BenchSettings(
            api_base="http://api.example/dev",
            localstack_endpoint="http://127.0.0.1:4566",
            image_bytes=b"jpeg-bytes",
            image_label="synthetic 1280x720 JPEG",
            content_type="image/jpeg",
            timeout_seconds=30.0,
            interval_seconds=0.05,
            runs=5,
            warmup=1,
            label=None,
            json_out=False,
        ),
        label=label,
        runs=runs,
        warmup=warmup,
        json_out=json_out,
    )


def _resolve_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_api_base(_explicit: str | None) -> str:
        return "http://api.example/dev"

    def fake_endpoint(_explicit: str | None) -> str:
        return "http://127.0.0.1:4566"

    monkeypatch.setattr(bench_module, "resolve_api_base", fake_api_base)
    monkeypatch.setattr(bench_module, "resolve_localstack_endpoint", fake_endpoint)


def test_metric_stats_odd_count() -> None:
    stats = metric_stats([3.0, 1.0, 2.0])
    assert stats.minimum == 1.0
    assert stats.median == 2.0
    assert stats.maximum == 3.0


def test_metric_stats_even_count() -> None:
    stats = metric_stats([4.0, 1.0, 2.0, 3.0])
    assert stats.minimum == 1.0
    assert stats.median == 2.5
    assert stats.maximum == 4.0


def test_metric_stats_single_value() -> None:
    stats = metric_stats([7.5])
    assert stats.minimum == 7.5
    assert stats.median == 7.5
    assert stats.maximum == 7.5


def test_metric_stats_rejects_empty() -> None:
    with pytest.raises(ValueError, match="at least one"):
        metric_stats([])


def test_synthetic_jpeg_is_1280x720() -> None:
    raw = synthetic_jpeg_bytes()
    image = Image.open(BytesIO(raw))
    assert image.format == "JPEG"
    assert image.size == (1280, 720)


def test_parse_defaults() -> None:
    args = parse_args([])
    assert args.image is None
    assert args.runs == 5
    assert args.warmup == 1
    assert args.interval == 0.05
    assert args.json is False
    assert args.label is None


def test_settings_synthetic_image(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_ok(monkeypatch)
    settings = settings_from_args(parse_args([]))
    assert settings.content_type == "image/jpeg"
    assert "1280x720" in settings.image_label
    image = Image.open(BytesIO(settings.image_bytes))
    assert image.size == (1280, 720)


def test_settings_reads_image_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _resolve_ok(monkeypatch)
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"not-really-jpeg")
    settings = settings_from_args(parse_args([str(path)]))
    assert settings.image_bytes == b"not-really-jpeg"
    assert settings.content_type == "image/jpeg"
    assert settings.image_label == str(path)


def test_settings_rejects_non_positive_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_ok(monkeypatch)
    args = parse_args(["--runs", "0"])
    with pytest.raises(CliError, match="--runs must be >= 1"):
        settings_from_args(args)


def test_settings_rejects_negative_warmup(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_ok(monkeypatch)
    args = parse_args(["--warmup", "-1"])
    with pytest.raises(CliError, match="--warmup must be >= 0"):
        settings_from_args(args)


def test_settings_rejects_non_positive_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_ok(monkeypatch)
    args = parse_args(["--interval", "0"])
    with pytest.raises(CliError, match="--interval must be positive"):
        settings_from_args(args)


def test_settings_rejects_non_positive_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_ok(monkeypatch)
    args = parse_args(["--timeout", "0"])
    with pytest.raises(CliError, match="--timeout must be positive"):
        settings_from_args(args)


def test_settings_rejects_missing_image(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_ok(monkeypatch)
    args = parse_args(["/no/such/photo.jpg"])
    with pytest.raises(CliError, match="image not found"):
        settings_from_args(args)


def test_run_once_records_timings(monkeypatch: pytest.MonkeyPatch) -> None:
    created = {
        "job_id": "abc",
        "upload_url": "http://localhost.localstack.cloud:4566/bucket/key",
        "input_key": "uploads/abc/original",
    }
    http_json = MagicMock(return_value=(201, created))
    http_put = MagicMock(return_value=200)
    poll = MagicMock(return_value={"status": "complete"})
    monkeypatch.setattr(bench_module, "http_json", http_json)
    monkeypatch.setattr(bench_module, "http_put", http_put)
    monkeypatch.setattr(bench_module, "poll_job_until_terminal", poll)

    row = run_once(_settings())

    assert row.status == "complete"
    assert row.post_ms >= 0
    assert row.put_ms >= 0
    assert row.upload_to_complete_ms >= 0
    assert row.e2e_ms >= row.post_ms
    http_json.assert_called_once()
    http_put.assert_called_once()
    put_url = http_put.call_args.kwargs.get("url") or http_put.call_args.args[0]
    assert put_url.startswith("http://127.0.0.1:4566/")
    poll.assert_called_once()


def test_run_once_rejects_bad_create_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bench_module, "http_json", MagicMock(return_value=(400, {"error": "nope"})))
    with pytest.raises(CliError, match="POST /jobs expected 201"):
        run_once(_settings())


def test_report_payload_and_table_include_summary() -> None:
    results = [
        BenchRun(
            post_ms=10.0,
            put_ms=2.0,
            upload_to_complete_ms=100.0,
            e2e_ms=112.0,
            status="complete",
        ),
        BenchRun(
            post_ms=30.0,
            put_ms=4.0,
            upload_to_complete_ms=300.0,
            e2e_ms=334.0,
            status="complete",
        ),
        BenchRun(
            post_ms=20.0,
            put_ms=3.0,
            upload_to_complete_ms=200.0,
            e2e_ms=223.0,
            status="failed",
        ),
    ]
    settings = _settings(label="before-esm", runs=3, warmup=1)
    payload = report_payload(settings=settings, results=results)
    assert payload["label"] == "before-esm"
    assert payload["summary"]["post_ms"] == {"min": 10.0, "median": 20.0, "max": 30.0}
    assert payload["summary"]["upload_to_complete_ms"] == {
        "min": 100.0,
        "median": 200.0,
        "max": 300.0,
    }
    table = format_table(settings=settings, results=results)
    assert "label: before-esm" in table
    assert "post_ms" in table
    assert "median=20.0" in table
    assert "median=200.0" in table
    assert "failed" in table


def test_main_json_and_failed_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = _settings(json_out=True, runs=1, warmup=0, label="after")

    def fake_settings(_args: Namespace) -> BenchSettings:
        return settings

    def fake_series(_ignored: BenchSettings) -> list[BenchRun]:
        return [
            BenchRun(
                post_ms=1.0,
                put_ms=1.0,
                upload_to_complete_ms=2.0,
                e2e_ms=4.0,
                status="failed",
            )
        ]

    monkeypatch.setattr(bench_module, "settings_from_args", fake_settings)
    monkeypatch.setattr(bench_module, "_run_series", fake_series)
    assert bench_module.main(["--json"]) == 1
    out = capsys.readouterr().out
    assert '"label": "after"' in out
    assert '"status": "failed"' in out


def test_main_cli_error_returns_1(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(_args: Namespace) -> BenchSettings:
        msg = "image not found: missing.jpg"
        raise CliError(msg)

    monkeypatch.setattr(bench_module, "settings_from_args", boom)
    assert bench_module.main(["missing.jpg"]) == 1
    err = capsys.readouterr().err
    assert "image not found" in err
