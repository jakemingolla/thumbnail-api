"""Create a job, upload an image, and poll until every size is terminal."""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse, urlunparse

from thumbnail_api.cli.local import (
    CliError,
    dump_job,
    http_json,
    http_put,
    require_str,
    resolve_api_base,
    resolve_localstack_endpoint,
)
from thumbnail_api.cli.style import (
    bold,
    cyan,
    dim,
    eprint,
    green,
    heading,
    human_bytes,
    kv,
    paint,
    red,
    size_sort_key,
    status_color,
    yellow,
)

_TERMINAL_STATUSES = frozenset({"complete", "failed"})
_DEFAULT_TIMEOUT_SECONDS = 120.0
_DEFAULT_POLL_INTERVAL_SECONDS = 1.0
_HTTP_OK = 200
_HTTP_CREATED = 201
_HTTP_NO_CONTENT = 204
_PUT_SUCCESS_STATUSES = frozenset({_HTTP_OK, _HTTP_NO_CONTENT})
_SPINNER_FRAMES = ("|", "/", "-", "\\")
_SIZE_BAR_WIDTH = 12
_PROCESSING_BLOCK_WIDTH = 3

_SUFFIX_TO_CONTENT_TYPE: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


@dataclass(frozen=True)
class _ProgressState:
    job_id: str
    overall: object
    size_statuses: dict[str, str]
    spinner: str
    frame: int


def _content_type_for_path(path: Path, override: str | None) -> str:
    if override is not None:
        return override
    content_type = _SUFFIX_TO_CONTENT_TYPE.get(path.suffix.lower())
    if content_type is None:
        allowed = ", ".join(sorted(_SUFFIX_TO_CONTENT_TYPE))
        msg = (
            f"cannot infer content_type from extension {path.suffix!r}; "
            f"use --content-type (allowed suffixes: {allowed})"
        )
        raise CliError(msg)
    return content_type


def _host_reachable_upload_url(upload_url: str, localstack_endpoint: str) -> str:
    """Rewrite in-Lambda LocalStack edge host to the host-reachable endpoint."""
    parsed = urlparse(upload_url)
    endpoint = urlparse(localstack_endpoint)
    if not endpoint.netloc:
        msg = f"LOCALSTACK_ENDPOINT has no netloc: {localstack_endpoint!r}"
        raise CliError(msg)
    if parsed.netloc == endpoint.netloc:
        return upload_url
    rewritten = urlunparse(
        parsed._replace(scheme=endpoint.scheme or "http", netloc=endpoint.netloc)
    )
    print(kv("rewrite", f"{dim(parsed.netloc)} → {cyan(endpoint.netloc)}"))
    return rewritten


def _size_statuses(job: dict[str, Any]) -> dict[str, str]:
    sizes_raw = job.get("sizes")
    if not isinstance(sizes_raw, dict):
        return {}
    sizes = cast("dict[str, Any]", sizes_raw)
    out: dict[str, str] = {}
    for key, entry in sizes.items():
        label = str(key)
        if isinstance(entry, dict):
            entry_map = cast("dict[str, Any]", entry)
            status = entry_map.get("status")
            if isinstance(status, str):
                out[label] = status
            elif status is None:
                out[label] = "?"
            else:
                out[label] = repr(status)
        else:
            out[label] = "?"
    return out


def _all_sizes_terminal(size_statuses: dict[str, str]) -> bool:
    if not size_statuses:
        return False
    return all(status in _TERMINAL_STATUSES for status in size_statuses.values())


def _is_fully_terminal(job: dict[str, Any]) -> bool:
    overall = job.get("status")
    if overall not in _TERMINAL_STATUSES:
        return False
    return _all_sizes_terminal(_size_statuses(job))


def _size_bar(status: str, *, frame: int) -> str:
    width = _SIZE_BAR_WIDTH
    if status == "complete":
        return f"[{green('#' * width)}]"
    if status == "failed":
        return f"[{red('!' * width)}]"
    if status == "processing":
        block = min(_PROCESSING_BLOCK_WIDTH, width)
        travel = max(width - block, 0)
        if travel == 0:
            return f"[{yellow('#' * width)}]"
        cycle = travel * 2
        pos = frame % cycle
        if pos > travel:
            pos = cycle - pos
        cells = ["-"] * width
        for i in range(block):
            cells[pos + i] = "#"
        inner = "".join(yellow(ch) if ch == "#" else dim(ch) for ch in cells)
        return f"[{inner}]"
    return f"[{dim('-' * width)}]"


def _format_progress_lines(state: _ProgressState) -> list[str]:
    done = sum(1 for status in state.size_statuses.values() if status in _TERMINAL_STATUSES)
    total = len(state.size_statuses)
    header = (
        f"  {cyan(state.spinner)} {bold('watching')}  "
        f"{dim(state.job_id[:8] + '…')}   "
        f"status {status_color(state.overall)}   "
        f"sizes {bold(f'{done}/{total}')}"
    )
    if not state.size_statuses:
        return [header, kv("", dim("(waiting for size statuses…)"))]

    label_width = max(len(size) for size in state.size_statuses)
    status_width = max(len(status) for status in state.size_statuses.values())
    col_size = max(label_width, 4)
    col_bar = _SIZE_BAR_WIDTH + 2
    col_status = max(status_width, 6)

    lines = [
        header,
        "",
        (
            f"  {dim('size'.ljust(col_size))}  "
            f"{dim('progress'.ljust(col_bar))}  "
            f"{dim('status'.ljust(col_status))}"
        ),
        (f"  {dim('─' * col_size)}  {dim('─' * col_bar)}  {dim('─' * col_status)}"),
    ]
    for size, status in sorted(state.size_statuses.items(), key=lambda i: size_sort_key(i[0])):
        bar = _size_bar(status, frame=state.frame)
        bare_bar_len = _SIZE_BAR_WIDTH + 2
        pad = " " * max(0, col_bar - bare_bar_len)
        lines.append(f"  {size.rjust(col_size)}  {bar}{pad}  {status_color(status)}")
    return lines


def _print_progress(
    state: _ProgressState,
    *,
    tty: bool,
    previous_line_count: int,
) -> int:
    lines = _format_progress_lines(state)
    block = "\n".join(lines)
    if tty:
        if previous_line_count > 0:
            sys.stdout.write(f"\033[{previous_line_count}A\r\033[J")
        sys.stdout.write(block + "\n")
        sys.stdout.flush()
        return len(lines)
    print(block)
    return 0


def _poll_until_terminal(
    *,
    api_base: str,
    job_id: str,
    timeout_seconds: float,
    interval_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    last_http: int | None = None
    last_signature: tuple[object, tuple[tuple[str, str], ...]] | None = None
    spin_idx = 0
    tty = sys.stdout.isatty()
    progress_line_count = 0

    while time.monotonic() < deadline:
        status_code, job = http_json("GET", f"{api_base}/jobs/{job_id}")
        last_http = status_code
        last = job
        if status_code != _HTTP_OK:
            msg = f"GET /jobs/{job_id} returned HTTP {status_code}:\n{dump_job(job)}"
            raise CliError(msg)

        size_statuses = _size_statuses(job)
        overall = job.get("status")
        signature = (overall, tuple(sorted(size_statuses.items())))
        spinner = _SPINNER_FRAMES[spin_idx % len(_SPINNER_FRAMES)]
        if tty or signature != last_signature:
            progress_line_count = _print_progress(
                _ProgressState(
                    job_id=job_id,
                    overall=overall,
                    size_statuses=size_statuses,
                    spinner=spinner,
                    frame=spin_idx,
                ),
                tty=tty,
                previous_line_count=progress_line_count,
            )
            last_signature = signature
        spin_idx += 1

        if _is_fully_terminal(job):
            return job

        time.sleep(interval_seconds)

    msg = (
        f"timeout: job {job_id} did not reach a terminal status for all sizes "
        f"within {timeout_seconds:g}s (api_base={api_base}, last_http={last_http}).\n"
        f"Last GET /jobs/{{id}} body:\n{dump_job(last)}"
    )
    raise CliError(msg)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="upload-watch",
        description=(
            "Create a thumbnail job, upload an image via the presigned URL, "
            "and poll until the job and every size are terminal."
        ),
    )
    parser.add_argument(
        "image",
        type=Path,
        help="Path to a JPEG, PNG, or WebP image to upload",
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
        choices=sorted(set(_SUFFIX_TO_CONTENT_TYPE.values())),
        help="Override Content-Type (default: infer from image extension)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_DEFAULT_TIMEOUT_SECONDS,
        help=f"Poll timeout in seconds (default: {_DEFAULT_TIMEOUT_SECONDS:g})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=_DEFAULT_POLL_INTERVAL_SECONDS,
        help=f"Poll interval in seconds (default: {_DEFAULT_POLL_INTERVAL_SECONDS:g})",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print full job JSON after a successful run",
    )
    return parser.parse_args(argv)


def _validate_args(args: argparse.Namespace) -> tuple[Path, str, str, str]:
    image_path: Path = args.image
    if not image_path.is_file():
        msg = f"image not found: {image_path}"
        raise CliError(msg)
    if args.timeout <= 0:
        msg = f"--timeout must be positive, got {args.timeout}"
        raise CliError(msg)
    if args.interval <= 0:
        msg = f"--interval must be positive, got {args.interval}"
        raise CliError(msg)

    content_type = _content_type_for_path(image_path, args.content_type)
    api_base = resolve_api_base(args.api_base)
    localstack_endpoint = resolve_localstack_endpoint(args.localstack_endpoint)
    return image_path, content_type, api_base, localstack_endpoint


def _create_and_upload(
    *,
    image_path: Path,
    content_type: str,
    api_base: str,
    localstack_endpoint: str,
) -> str:
    print(heading("upload-watch"))
    print(kv("api", api_base))
    print(kv("image", f"{image_path}  {dim(f'({content_type})')}"))
    print()

    create_status, created = http_json(
        "POST",
        f"{api_base}/jobs",
        body={"content_type": content_type},
    )
    if create_status != _HTTP_CREATED:
        msg = f"POST /jobs expected {_HTTP_CREATED}, got {create_status}:\n{dump_job(created)}"
        raise CliError(msg)

    job_id = require_str(created.get("job_id"), field="job_id", context=created)
    upload_url = require_str(created.get("upload_url"), field="upload_url", context=created)
    input_key = require_str(created.get("input_key"), field="input_key", context=created)
    print(f"  {green('✓')} {bold('created')}   {job_id}")
    print(kv("status", status_color(created.get("status"))))
    print(kv("input", dim(input_key)))

    put_url = _host_reachable_upload_url(upload_url, localstack_endpoint)
    image_bytes = image_path.read_bytes()
    put_status = http_put(put_url, body=image_bytes, content_type=content_type)
    if put_status not in _PUT_SUCCESS_STATUSES:
        msg = f"presigned PUT expected 200/204, got {put_status} (url={put_url})"
        raise CliError(msg)
    print(
        f"  {green('✓')} {bold('uploaded')}  "
        f"{human_bytes(len(image_bytes))}  {dim(f'(HTTP {put_status})')}"
    )
    print()
    return job_id


def _report_outcome(job_id: str, job: dict[str, Any], *, verbose: bool) -> int:
    overall = job.get("status")
    size_statuses = _size_statuses(job)
    print()
    if overall == "complete" and all(status == "complete" for status in size_statuses.values()):
        print(f"  {green('✓')} {bold('complete')}  {job_id}")
    elif overall == "failed" or any(status == "failed" for status in size_statuses.values()):
        print(f"  {red('✗')} {bold('failed')}    {job_id}")
    else:
        print(f"  {yellow('•')} {bold(str(overall))}  {job_id}")

    label_width = max((len(size) for size in size_statuses), default=4)
    for size, status in sorted(size_statuses.items(), key=lambda i: size_sort_key(i[0])):
        print(f"  {dim('size')} {size.rjust(label_width)}  {status_color(status)}")

    if verbose or overall != "complete":
        print()
        print(dim(dump_job(job)))

    if overall == "complete" and all(status == "complete" for status in size_statuses.values()):
        print()
        print(f"  {green('OK')}  job complete")
        print(kv("next", f"just download-job {job_id}"))
        return 0

    eprint(red("error: job finished with failure"))
    return 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        image_path, content_type, api_base, localstack_endpoint = _validate_args(args)
        job_id = _create_and_upload(
            image_path=image_path,
            content_type=content_type,
            api_base=api_base,
            localstack_endpoint=localstack_endpoint,
        )
        job = _poll_until_terminal(
            api_base=api_base,
            job_id=job_id,
            timeout_seconds=args.timeout,
            interval_seconds=args.interval,
        )
    except CliError as exc:
        eprint(paint(f"error: {exc}", "\033[31m", stream=sys.stderr))
        return 1

    return _report_outcome(job_id, job, verbose=bool(args.verbose))
