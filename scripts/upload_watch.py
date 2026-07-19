#!/usr/bin/env python3
"""Create a job, upload an image, and poll until every size is terminal.

Interactive verify helper for the local stack (``just upload-watch``). Not a
replacement for the CI e2e harness (``just test-e2e``).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse, urlunparse

_TERMINAL_STATUSES = frozenset({"complete", "failed"})
_DEFAULT_TIMEOUT_SECONDS = 120.0
_DEFAULT_POLL_INTERVAL_SECONDS = 1.0
_HTTP_OK = 200
_HTTP_CREATED = 201
_HTTP_NO_CONTENT = 204
_PUT_SUCCESS_STATUSES = frozenset({_HTTP_OK, _HTTP_NO_CONTENT})
_SPINNER_FRAMES = ("|", "/", "-", "\\")

_SUFFIX_TO_CONTENT_TYPE: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

_REPO_ROOT = Path(__file__).resolve().parent.parent


class UploadWatchError(Exception):
    """User-facing failure with a clear message (exit non-zero)."""


@dataclass(frozen=True)
class _ProgressState:
    job_id: str
    overall: object
    size_statuses: dict[str, str]
    spinner: str


def _eprint(message: str) -> None:
    print(message, file=sys.stderr)


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
        raise UploadWatchError(msg)
    return content_type


def _tf_raw(output_name: str) -> str:
    state = _REPO_ROOT / "infra" / "terraform.tfstate"
    if not state.is_file():
        msg = (
            f"{state} missing — apply the stack first: just apply "
            "(or set API_BASE / LOCALSTACK_ENDPOINT)"
        )
        raise UploadWatchError(msg)
    terraform = shutil.which("terraform")
    if terraform is None:
        msg = "terraform is required on PATH (or set API_BASE / LOCALSTACK_ENDPOINT)"
        raise UploadWatchError(msg)
    try:
        completed = subprocess.run(  # noqa: S603 — resolved terraform path + fixed args
            [terraform, "output", "-raw", output_name],
            cwd=_REPO_ROOT / "infra",
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        msg = f"terraform output -raw {output_name} failed"
        if detail:
            msg = f"{msg}: {detail}"
        raise UploadWatchError(msg) from exc
    value = completed.stdout.strip()
    if not value:
        msg = f"terraform output {output_name} was empty"
        raise UploadWatchError(msg)
    return value


def _resolve_api_base(explicit: str | None) -> str:
    if explicit:
        return explicit.rstrip("/")
    env = os.environ.get("API_BASE", "").strip()
    if env:
        return env.rstrip("/")
    return _tf_raw("api_base_url").rstrip("/")


def _resolve_localstack_endpoint(explicit: str | None) -> str:
    if explicit:
        return explicit.rstrip("/")
    env = os.environ.get("LOCALSTACK_ENDPOINT", "").strip()
    if env:
        return env.rstrip("/")
    return _tf_raw("localstack_endpoint").rstrip("/")


def _host_reachable_upload_url(upload_url: str, localstack_endpoint: str) -> str:
    """Rewrite in-Lambda LocalStack edge host to the host-reachable endpoint.

    ``create_job`` signs URLs against ``AWS_ENDPOINT_URL`` inside Lambda
    (``http://localhost.localstack.cloud:4566``). Host clients must PUT against
    ``LOCALSTACK_ENDPOINT`` (``http://127.0.0.1:<edge-port>``). Keep path-style.
    """
    parsed = urlparse(upload_url)
    endpoint = urlparse(localstack_endpoint)
    if not endpoint.netloc:
        msg = f"LOCALSTACK_ENDPOINT has no netloc: {localstack_endpoint!r}"
        raise UploadWatchError(msg)
    if parsed.netloc == endpoint.netloc:
        return upload_url
    rewritten = urlunparse(
        parsed._replace(scheme=endpoint.scheme or "http", netloc=endpoint.netloc)
    )
    print(f"rewrote upload_url host {parsed.netloc!r} → {endpoint.netloc!r}")
    return rewritten


def _http_json(
    method: str,
    url: str,
    *,
    body: dict[str, object] | None = None,
    timeout: float = 30,
) -> tuple[int, dict[str, Any]]:
    data: bytes | None = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(  # noqa: S310 — local verify against LocalStack
        url, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            status = int(resp.status)
            raw = resp.read().decode()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read().decode(errors="replace")
    except TimeoutError as exc:
        msg = f"HTTP {method} {url} timed out after {timeout}s: {exc}"
        raise UploadWatchError(msg) from exc
    except urllib.error.URLError as exc:
        msg = f"HTTP {method} {url} failed: {exc}"
        raise UploadWatchError(msg) from exc

    try:
        payload: object = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        msg = f"HTTP {method} {url} returned non-JSON (status={status}): {exc}\n{raw}"
        raise UploadWatchError(msg) from exc

    if not isinstance(payload, dict):
        msg = f"HTTP {method} {url} JSON was not an object (status={status}): {payload!r}"
        raise UploadWatchError(msg)
    return status, cast("dict[str, Any]", payload)


def _http_put(
    url: str,
    *,
    body: bytes,
    content_type: str,
    timeout: float = 60,
) -> int:
    request = urllib.request.Request(  # noqa: S310 — local verify against LocalStack
        url,
        data=body,
        headers={"Content-Type": content_type},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode(errors="replace")
        msg = f"HTTP PUT upload failed status={exc.code} url={url}\n{err_body}"
        raise UploadWatchError(msg) from exc
    except TimeoutError as exc:
        msg = f"HTTP PUT upload timed out after {timeout}s url={url}: {exc}"
        raise UploadWatchError(msg) from exc
    except urllib.error.URLError as exc:
        msg = f"HTTP PUT upload failed url={url}: {exc}"
        raise UploadWatchError(msg) from exc


def _require_str(value: object, *, field: str, context: object) -> str:
    if not isinstance(value, str) or not value:
        msg = f"missing or empty {field}: {context}"
        raise UploadWatchError(msg)
    return value


def _size_statuses(job: dict[str, Any]) -> dict[str, str]:
    sizes_raw = job.get("sizes")
    if not isinstance(sizes_raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, entry in sizes_raw.items():
        label = str(key)
        if isinstance(entry, dict):
            status = entry.get("status")
            out[label] = str(status) if status is not None else "?"
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


def _format_progress_line(state: _ProgressState) -> str:
    size_bits = " ".join(f"{size}={status}" for size, status in sorted(state.size_statuses.items()))
    if not size_bits:
        size_bits = "(no sizes yet)"
    done = sum(1 for status in state.size_statuses.values() if status in _TERMINAL_STATUSES)
    total = len(state.size_statuses)
    bar_width = max(total, 1)
    filled = done if total else 0
    bar = "#" * filled + "-" * (bar_width - filled)
    return (
        f"{state.spinner} job={state.job_id[:8]}… status={state.overall} "
        f"sizes[{done}/{total}] [{bar}] {size_bits}"
    )


def _print_progress(
    state: _ProgressState,
    *,
    tty: bool,
    last_line_len: int,
) -> int:
    line = _format_progress_line(state)
    if tty:
        pad = max(0, last_line_len - len(line))
        sys.stdout.write("\r" + line + (" " * pad))
        sys.stdout.flush()
        return len(line)
    print(line)
    return last_line_len


def _dump_job(job: dict[str, Any] | None) -> str:
    if job is None:
        return "<no response>"
    return json.dumps(job, indent=2, default=str)


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
    last_line_len = 0

    while time.monotonic() < deadline:
        status_code, job = _http_json("GET", f"{api_base}/jobs/{job_id}")
        last_http = status_code
        last = job
        if status_code != _HTTP_OK:
            msg = f"GET /jobs/{job_id} returned HTTP {status_code}:\n{_dump_job(job)}"
            raise UploadWatchError(msg)

        size_statuses = _size_statuses(job)
        overall = job.get("status")
        signature = (overall, tuple(sorted(size_statuses.items())))
        spinner = _SPINNER_FRAMES[spin_idx % len(_SPINNER_FRAMES)]
        spin_idx += 1

        if tty or signature != last_signature:
            last_line_len = _print_progress(
                _ProgressState(
                    job_id=job_id,
                    overall=overall,
                    size_statuses=size_statuses,
                    spinner=spinner,
                ),
                tty=tty,
                last_line_len=last_line_len,
            )
            last_signature = signature

        if _is_fully_terminal(job):
            if tty:
                sys.stdout.write("\n")
                sys.stdout.flush()
            return job

        time.sleep(interval_seconds)

    if tty and last_line_len:
        sys.stdout.write("\n")
        sys.stdout.flush()

    msg = (
        f"timeout: job {job_id} did not reach a terminal status for all sizes "
        f"within {timeout_seconds:g}s (api_base={api_base}, last_http={last_http}).\n"
        f"Last GET /jobs/{{id}} body:\n{_dump_job(last)}"
    )
    raise UploadWatchError(msg)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
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
    return parser.parse_args(argv)


def _validate_args(args: argparse.Namespace) -> tuple[Path, str, str, str]:
    image_path: Path = args.image
    if not image_path.is_file():
        msg = f"image not found: {image_path}"
        raise UploadWatchError(msg)
    if args.timeout <= 0:
        msg = f"--timeout must be positive, got {args.timeout}"
        raise UploadWatchError(msg)
    if args.interval <= 0:
        msg = f"--interval must be positive, got {args.interval}"
        raise UploadWatchError(msg)

    content_type = _content_type_for_path(image_path, args.content_type)
    api_base = _resolve_api_base(args.api_base)
    localstack_endpoint = _resolve_localstack_endpoint(args.localstack_endpoint)
    return image_path, content_type, api_base, localstack_endpoint


def _create_and_upload(
    *,
    image_path: Path,
    content_type: str,
    api_base: str,
    localstack_endpoint: str,
) -> str:
    print(f"API_BASE={api_base}")
    print(f"uploading {image_path} ({content_type})")

    create_status, created = _http_json(
        "POST",
        f"{api_base}/jobs",
        body={"content_type": content_type},
    )
    if create_status != _HTTP_CREATED:
        msg = f"POST /jobs expected {_HTTP_CREATED}, got {create_status}:\n{_dump_job(created)}"
        raise UploadWatchError(msg)

    job_id = _require_str(created.get("job_id"), field="job_id", context=created)
    upload_url = _require_str(created.get("upload_url"), field="upload_url", context=created)
    input_key = _require_str(created.get("input_key"), field="input_key", context=created)
    print(f"created job_id={job_id} status={created.get('status')} input_key={input_key}")

    put_url = _host_reachable_upload_url(upload_url, localstack_endpoint)
    image_bytes = image_path.read_bytes()
    put_status = _http_put(put_url, body=image_bytes, content_type=content_type)
    if put_status not in _PUT_SUCCESS_STATUSES:
        msg = f"presigned PUT expected 200/204, got {put_status} (url={put_url})"
        raise UploadWatchError(msg)
    print(f"uploaded {len(image_bytes)} bytes (HTTP {put_status})")
    return job_id


def _report_outcome(job_id: str, job: dict[str, Any]) -> int:
    overall = job.get("status")
    size_statuses = _size_statuses(job)
    print(f"job {job_id} → {overall}")
    for size, status in sorted(size_statuses.items()):
        print(f"  size {size}: {status}")
    print(_dump_job(job))

    if overall == "complete" and all(status == "complete" for status in size_statuses.values()):
        print("OK: job complete")
        return 0

    _eprint("error: job finished with failure")
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
    except UploadWatchError as exc:
        _eprint(f"error: {exc}")
        return 1

    return _report_outcome(job_id, job)


if __name__ == "__main__":
    raise SystemExit(main())
