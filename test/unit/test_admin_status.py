"""Unit tests for admin-status graphs and report formatting."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from thumbnail_api.cli.admin_status import (
    AdminClients,
    BucketStats,
    History,
    PrefixStats,
    QueueDepth,
    Snapshot,
    StackTargets,
    bucket_total_bytes,
    collect_snapshot,
    format_report,
)
from thumbnail_api.cli.style import ascii_bar, human_bytes, sparkline

if TYPE_CHECKING:
    import pytest


def test_ascii_bar_scales_and_clamps() -> None:
    assert ascii_bar(0, width=10, scale=10) == "░░░░░░░░░░"
    assert ascii_bar(5, width=10, scale=10) == "█████░░░░░"
    assert ascii_bar(10, width=10, scale=10) == "██████████"
    assert ascii_bar(50, width=10, scale=10) == "██████████"


def test_sparkline_pads_and_tracks_shape() -> None:
    assert sparkline([], width=4) == "▁▁▁▁"
    assert sparkline([0, 0, 0], width=3) == "▁▁▁"
    rising = sparkline([0, 1, 2, 3, 4], width=5)
    assert len(rising) == 5
    assert rising[0] <= rising[-1]


def test_human_bytes_labels() -> None:
    assert human_bytes(0) == "0 B"
    assert human_bytes(512) == "512 B"
    assert human_bytes(1536) == "1.5 KiB"
    assert human_bytes(1024 * 1024) == "1.0 MiB"


def _snapshot(**overrides: int) -> Snapshot:
    work_visible = overrides.get("work_visible", 0)
    work_in_flight = overrides.get("work_in_flight", 0)
    dlq_visible = overrides.get("dlq_visible", 0)
    job_count = overrides.get("job_count", 0)
    input_count = overrides.get("input_count", 0)
    input_bytes = overrides.get("input_bytes", 0)
    output_count = overrides.get("output_count", 0)
    output_bytes = overrides.get("output_bytes", 0)
    return Snapshot(
        endpoint="http://127.0.0.1:4566",
        region="us-east-1",
        work_queue_url="http://127.0.0.1:4566/000000000000/thumbnail-work",
        dlq_url="http://127.0.0.1:4566/000000000000/thumbnail-work-dlq",
        work=QueueDepth(visible=work_visible, in_flight=work_in_flight),
        dlq=QueueDepth(visible=dlq_visible, in_flight=0),
        jobs_table="thumbnail-jobs",
        job_count=job_count,
        input_bucket=BucketStats(
            name="thumbnail-input",
            primary_prefix="uploads/",
            primary=PrefixStats(count=input_count, size_bytes=input_bytes),
            other=PrefixStats(count=0, size_bytes=0),
        ),
        output_bucket=BucketStats(
            name="thumbnail-output",
            primary_prefix="thumbnails/",
            primary=PrefixStats(count=output_count, size_bytes=output_bytes),
            other=PrefixStats(count=0, size_bytes=0),
        ),
    )


def test_format_report_quiet_stack_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    text = format_report(_snapshot())
    assert "Admin status" in text
    assert "SQS" in text
    assert "work-dlq" in text
    assert "[ok]" in text
    assert "DynamoDB  thumbnail-jobs" in text
    assert "(scan count)" in text
    assert "S3  thumbnail-input  0 objs · 0 B" in text
    assert "S3  thumbnail-output  0 objs · 0 B" in text
    assert "\033[" not in text


def test_format_report_dlq_warn_and_sizes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    text = format_report(
        _snapshot(
            dlq_visible=2,
            job_count=5,
            input_count=2,
            input_bytes=655360,
            output_count=3,
            output_bytes=215142,
        )
    )
    assert "[WARN]" in text
    assert "2 objs · 640.0 KiB" in text
    assert "3 objs · 210.1 KiB" in text
    assert "uploads/" in text
    assert "thumbnails/" in text


def test_format_report_watch_includes_sparklines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    history = History.create(8)
    snap = _snapshot(work_visible=3, work_in_flight=1, job_count=4)
    history.append(snap)
    history.append(_snapshot(work_visible=2, work_in_flight=2, job_count=5))
    text = format_report(snap, history=history, interval_seconds=2)
    assert "watch  every 2s · last 8 samples" in text
    assert "in-flight" in text
    assert any(ch in text for ch in "▁▂▃▄▅▆▇█")
    assert "no sparkline" in text


class _FakeSqs:
    def __init__(self, depths: dict[str, tuple[int, int]]) -> None:
        self._depths = depths

    def get_queue_attributes(self, **kwargs: object) -> dict[str, Any]:
        url = str(kwargs["QueueUrl"])
        visible, in_flight = self._depths[url]
        return {
            "Attributes": {
                "ApproximateNumberOfMessages": str(visible),
                "ApproximateNumberOfMessagesNotVisible": str(in_flight),
            }
        }


class _FakeDynamo:
    def __init__(self, count: int) -> None:
        self._count = count

    def scan(self, **kwargs: object) -> dict[str, Any]:
        assert kwargs.get("Select") == "COUNT"
        return {"Count": self._count}


class _FakeS3:
    def __init__(self, objects: dict[str, list[tuple[str, int]]]) -> None:
        self._objects = objects

    def list_objects_v2(self, **kwargs: object) -> dict[str, Any]:
        bucket = str(kwargs["Bucket"])
        contents = [{"Key": key, "Size": size} for key, size in self._objects.get(bucket, [])]
        return {"Contents": contents, "IsTruncated": False}


def test_collect_snapshot_aggregates_resources() -> None:
    work = "http://sqs/work"
    dlq = "http://sqs/dlq"
    targets = StackTargets(
        endpoint="http://127.0.0.1:4566",
        work_queue_url=work,
        dlq_url=dlq,
        jobs_table="thumbnail-jobs",
        input_bucket="thumbnail-input",
        output_bucket="thumbnail-output",
    )
    snap = collect_snapshot(
        targets,
        clients=AdminClients(
            sqs=_FakeSqs({work: (3, 1), dlq: (0, 0)}),
            dynamodb=_FakeDynamo(7),
            s3=_FakeS3(
                {
                    "thumbnail-input": [
                        ("uploads/j1/original", 100),
                        ("uploads/j2/original", 50),
                        ("noise.txt", 5),
                    ],
                    "thumbnail-output": [
                        ("thumbnails/j1/128.jpg", 20),
                        ("thumbnails/j1/256.jpg", 40),
                    ],
                }
            ),
        ),
    )
    assert snap.work.visible == 3
    assert snap.work.in_flight == 1
    assert snap.dlq.visible == 0
    assert snap.job_count == 7
    assert snap.input_bucket.primary.count == 2
    assert snap.input_bucket.primary.size_bytes == 150
    assert snap.input_bucket.other.count == 1
    assert bucket_total_bytes(snap.input_bucket) == 155
    assert snap.output_bucket.primary.count == 2
    assert bucket_total_bytes(snap.output_bucket) == 60
