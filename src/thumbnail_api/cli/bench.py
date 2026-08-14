"""Time POST /jobs → presigned PUT → poll-to-terminal across N runs."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from thumbnail_api.cli.local import (
    HTTP_CREATED,
    PUT_SUCCESS_STATUSES,
    SUFFIX_TO_CONTENT_TYPE,
    CliError,
    content_type_for_path,
    dump_job,
    http_json,
    http_put,
    poll_job_until_terminal,
    require_str,
    resolve_api_base,
    resolve_localstack_endpoint,
    rewrite_upload_url,
)
from thumbnail_api.cli.style import eprint, paint

_DEFAULT_RUNS = 5
_DEFAULT_WARMUP = 1
_DEFAULT_TIMEOUT_SECONDS = 120.0
_DEFAULT_POLL_INTERVAL_SECONDS = 0.05
_SYNTHETIC_WIDTH = 1280
_SYNTHETIC_HEIGHT = 720
_SYNTHETIC_CONTENT_TYPE = "image/jpeg"


@dataclass(frozen=True)
class BenchSettings:
    api_base: str
    localstack_endpoint: str
    image_bytes: bytes
    image_label: str
    content_type: str
    timeout_seconds: float
    interval_seconds: float
    runs: int
    warmup: int
    label: str | None
    json_out: bool


@dataclass(frozen=True)
class BenchRun:
    post_ms: float
    put_ms: float
    upload_to_complete_ms: float
    e2e_ms: float
    status: str


@dataclass(frozen=True)
class MetricStats:
    minimum: float
    median: float
    maximum: float


def metric_stats(values: list[float]) -> MetricStats:
    """Return min / median / max. ``values`` must be non-empty."""
    if not values:
        msg = "metric_stats requires at least one value"
        raise ValueError(msg)
    ordered = sorted(values)
    return MetricStats(
        minimum=ordered[0],
        median=float(statistics.median(ordered)),
        maximum=ordered[-1],
    )


def synthetic_jpeg_bytes(
    *,
    width: int = _SYNTHETIC_WIDTH,
    height: int = _SYNTHETIC_HEIGHT,
) -> bytes:
    """Generate a solid-color JPEG for local latency runs (no fixture file)."""
    image = Image.new("RGB", (width, height), color=(40, 120, 200))
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _ms(start: float, end: float) -> float:
    return (end - start) * 1000.0


def run_once(settings: BenchSettings) -> BenchRun:
    """Create a job, PUT the image, poll until terminal; return phase timings."""
    t0 = time.monotonic()
    create_status, created = http_json(
        "POST",
        f"{settings.api_base}/jobs",
        body={"content_type": settings.content_type},
    )
    t_post = time.monotonic()
    if create_status != HTTP_CREATED:
        msg = f"POST /jobs expected {HTTP_CREATED}, got {create_status}:\n{dump_job(created)}"
        raise CliError(msg)

    job_id = require_str(created.get("job_id"), field="job_id", context=created)
    upload_url = require_str(created.get("upload_url"), field="upload_url", context=created)
    put_url = rewrite_upload_url(upload_url, settings.localstack_endpoint)

    t_put0 = time.monotonic()
    put_status = http_put(
        put_url,
        body=settings.image_bytes,
        content_type=settings.content_type,
    )
    t_put1 = time.monotonic()
    if put_status not in PUT_SUCCESS_STATUSES:
        msg = f"presigned PUT expected 200/204, got {put_status} (url={put_url})"
        raise CliError(msg)

    job = poll_job_until_terminal(
        api_base=settings.api_base,
        job_id=job_id,
        timeout_seconds=settings.timeout_seconds,
        interval_seconds=settings.interval_seconds,
    )
    t_end = time.monotonic()

    status_obj = job.get("status")
    status = status_obj if isinstance(status_obj, str) else repr(status_obj)
    return BenchRun(
        post_ms=_ms(t0, t_post),
        put_ms=_ms(t_put0, t_put1),
        upload_to_complete_ms=_ms(t_put1, t_end),
        e2e_ms=_ms(t0, t_end),
        status=status,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bench",
        description=(
            "Time POST /jobs, the presigned PUT, and poll-until-terminal "
            "across N runs (default: synthetic 1280x720 JPEG)."
        ),
    )
    parser.add_argument(
        "image",
        nargs="?",
        type=Path,
        default=None,
        help="Image to upload (default: generate a 1280x720 JPEG in memory)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=_DEFAULT_RUNS,
        help=f"Timed runs to report (default: {_DEFAULT_RUNS})",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=_DEFAULT_WARMUP,
        help=f"Discarded runs before timing (default: {_DEFAULT_WARMUP})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a JSON report instead of a table",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="Optional label stored on the report (e.g. before-esm)",
    )
    parser.add_argument(
        "--api-base",
        default=None,
        help="API base URL (default: $API_BASE or terraform output api_base_url)",
    )
    parser.add_argument(
        "--localstack-endpoint",
        default=None,
        help=(
            "Host-reachable LocalStack edge for rewriting upload_url "
            "(default: $LOCALSTACK_ENDPOINT or terraform output)"
        ),
    )
    parser.add_argument(
        "--content-type",
        default=None,
        choices=sorted(set(SUFFIX_TO_CONTENT_TYPE.values())),
        help="Override Content-Type (default: infer from image, or image/jpeg)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_DEFAULT_TIMEOUT_SECONDS,
        help=f"Per-run poll timeout in seconds (default: {_DEFAULT_TIMEOUT_SECONDS:g})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=_DEFAULT_POLL_INTERVAL_SECONDS,
        help=f"Poll interval in seconds (default: {_DEFAULT_POLL_INTERVAL_SECONDS:g})",
    )
    return parser.parse_args(argv)


def settings_from_args(args: argparse.Namespace) -> BenchSettings:
    if args.runs < 1:
        msg = f"--runs must be >= 1, got {args.runs}"
        raise CliError(msg)
    if args.warmup < 0:
        msg = f"--warmup must be >= 0, got {args.warmup}"
        raise CliError(msg)
    if args.timeout <= 0:
        msg = f"--timeout must be positive, got {args.timeout}"
        raise CliError(msg)
    if args.interval <= 0:
        msg = f"--interval must be positive, got {args.interval}"
        raise CliError(msg)

    image_path: Path | None = args.image
    if image_path is None:
        image_bytes = synthetic_jpeg_bytes()
        image_label = f"synthetic {_SYNTHETIC_WIDTH}x{_SYNTHETIC_HEIGHT} JPEG"
        content_type = args.content_type or _SYNTHETIC_CONTENT_TYPE
    else:
        if not image_path.is_file():
            msg = f"image not found: {image_path}"
            raise CliError(msg)
        image_bytes = image_path.read_bytes()
        image_label = str(image_path)
        content_type = content_type_for_path(image_path, args.content_type)

    return BenchSettings(
        api_base=resolve_api_base(args.api_base),
        localstack_endpoint=resolve_localstack_endpoint(args.localstack_endpoint),
        image_bytes=image_bytes,
        image_label=image_label,
        content_type=content_type,
        timeout_seconds=args.timeout,
        interval_seconds=args.interval,
        runs=args.runs,
        warmup=args.warmup,
        label=args.label,
        json_out=bool(args.json),
    )


def _round_ms(value: float) -> float:
    return round(value, 3)


def report_payload(
    *,
    settings: BenchSettings,
    results: list[BenchRun],
) -> dict[str, Any]:
    post = metric_stats([row.post_ms for row in results])
    upload = metric_stats([row.upload_to_complete_ms for row in results])
    return {
        "label": settings.label,
        "image": settings.image_label,
        "runs": settings.runs,
        "warmup": settings.warmup,
        "interval_seconds": settings.interval_seconds,
        "results": [
            {
                "run": index,
                "post_ms": _round_ms(row.post_ms),
                "put_ms": _round_ms(row.put_ms),
                "upload_to_complete_ms": _round_ms(row.upload_to_complete_ms),
                "e2e_ms": _round_ms(row.e2e_ms),
                "status": row.status,
            }
            for index, row in enumerate(results, start=1)
        ],
        "summary": {
            "post_ms": {
                "min": _round_ms(post.minimum),
                "median": _round_ms(post.median),
                "max": _round_ms(post.maximum),
            },
            "upload_to_complete_ms": {
                "min": _round_ms(upload.minimum),
                "median": _round_ms(upload.median),
                "max": _round_ms(upload.maximum),
            },
        },
    }


def format_table(
    *,
    settings: BenchSettings,
    results: list[BenchRun],
) -> str:
    lines: list[str] = []
    if settings.label:
        lines.append(f"label: {settings.label}")
    lines.append(f"image: {settings.image_label}")
    lines.append(
        f"runs: {settings.runs}  warmup: {settings.warmup}  "
        f"interval: {settings.interval_seconds:g}s"
    )
    lines.append("")
    lines.append(
        f"{'run':>4}  {'post_ms':>10}  {'put_ms':>10}  "
        f"{'upload_to_complete_ms':>22}  {'e2e_ms':>10}  status"
    )
    lines.extend(
        f"{index:4d}  {row.post_ms:10.1f}  {row.put_ms:10.1f}  "
        f"{row.upload_to_complete_ms:22.1f}  {row.e2e_ms:10.1f}  {row.status}"
        for index, row in enumerate(results, start=1)
    )
    post = metric_stats([row.post_ms for row in results])
    upload = metric_stats([row.upload_to_complete_ms for row in results])
    lines.append("")
    lines.append(
        f"{'post_ms':<24} min={post.minimum:.1f}  median={post.median:.1f}  max={post.maximum:.1f}"
    )
    lines.append(
        f"{'upload_to_complete_ms':<24} min={upload.minimum:.1f}  "
        f"median={upload.median:.1f}  max={upload.maximum:.1f}"
    )
    return "\n".join(lines)


def _run_series(settings: BenchSettings) -> list[BenchRun]:
    for index in range(1, settings.warmup + 1):
        if not settings.json_out:
            print(f"warmup {index}/{settings.warmup} …", file=sys.stderr)
        run_once(settings)

    results: list[BenchRun] = []
    for index in range(1, settings.runs + 1):
        if not settings.json_out:
            print(f"run {index}/{settings.runs} …", file=sys.stderr)
        results.append(run_once(settings))
    return results


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = settings_from_args(args)
        results = _run_series(settings)
    except CliError as exc:
        eprint(paint(f"error: {exc}", "\033[31m", stream=sys.stderr))
        return 1

    if settings.json_out:
        print(json.dumps(report_payload(settings=settings, results=results), indent=2))
    else:
        print(format_table(settings=settings, results=results))

    if any(row.status != "complete" for row in results):
        eprint("error: one or more timed runs did not complete")
        return 1
    return 0
