"""Shared LocalStack / terraform / HTTP helpers for verify CLIs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, cast


class CliError(Exception):
    """User-facing CLI failure (exit non-zero)."""


def repo_root() -> Path:
    """Resolve the git worktree root (directory containing ``infra/``)."""
    cwd = Path.cwd()
    if (cwd / "infra").is_dir():
        return cwd
    for parent in Path(__file__).resolve().parents:
        if (parent / "infra").is_dir() and (parent / "justfile").is_file():
            return parent
    return cwd


def tf_raw(output_name: str) -> str:
    root = repo_root()
    state = root / "infra" / "terraform.tfstate"
    if not state.is_file():
        msg = (
            f"{state} missing — apply the stack first: just apply "
            "(or set LOCALSTACK_ENDPOINT / resource env vars)"
        )
        raise CliError(msg)
    terraform = shutil.which("terraform")
    if terraform is None:
        msg = "terraform is required on PATH (or set LOCALSTACK_ENDPOINT / resource env vars)"
        raise CliError(msg)
    try:
        completed = subprocess.run(  # noqa: S603 — resolved terraform path + fixed args
            [terraform, "output", "-raw", output_name],
            cwd=root / "infra",
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        msg = f"terraform output -raw {output_name} failed"
        if detail:
            msg = f"{msg}: {detail}"
        raise CliError(msg) from exc
    value = completed.stdout.strip()
    if not value:
        msg = f"terraform output {output_name} was empty"
        raise CliError(msg)
    return value


def resolve_api_base(explicit: str | None) -> str:
    if explicit:
        return explicit.rstrip("/")
    env = os.environ.get("API_BASE", "").strip()
    if env:
        return env.rstrip("/")
    return tf_raw("api_base_url").rstrip("/")


def resolve_localstack_endpoint(explicit: str | None) -> str:
    if explicit:
        return explicit.rstrip("/")
    env = os.environ.get("LOCALSTACK_ENDPOINT", "").strip()
    if env:
        return env.rstrip("/")
    return tf_raw("localstack_endpoint").rstrip("/")


def resolve_output_bucket(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("OUTPUT_BUCKET", "").strip()
    if env:
        return env
    return tf_raw("output_bucket_name")


def resolve_input_bucket(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("INPUT_BUCKET", "").strip()
    if env:
        return env
    return tf_raw("input_bucket_name")


def resolve_jobs_table(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("JOBS_TABLE", "").strip()
    if env:
        return env
    return tf_raw("jobs_table_name")


def resolve_work_queue_url(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("QUEUE_URL", "").strip()
    if env:
        return env
    return tf_raw("work_queue_url")


def resolve_work_dlq_url(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("WORK_DLQ_URL", "").strip()
    if env:
        return env
    return tf_raw("work_dlq_url")


def aws_region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


_HTTP_OK = 200
_HTTP_BAD_REQUEST = 400


def ensure_localstack_healthy(endpoint: str, *, timeout: float = 3.0) -> None:
    """Fail clearly when the LocalStack edge is down or unreachable."""
    url = f"{endpoint.rstrip('/')}/_localstack/health"
    request = urllib.request.Request(url, method="GET")  # noqa: S310 — local edge
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            status = int(resp.status)
    except TimeoutError as exc:
        msg = (
            f"LocalStack health check timed out at {endpoint}\n"
            "  Start or recreate: just localstack-up\n"
            f'  Health: curl -sf "{endpoint}/_localstack/health" | jq .'
        )
        raise CliError(msg) from exc
    except urllib.error.URLError as exc:
        msg = (
            f"LocalStack is not healthy at {endpoint}: {exc.reason}\n"
            "  Start or recreate: just localstack-up\n"
            f'  Health: curl -sf "{endpoint}/_localstack/health" | jq .'
        )
        raise CliError(msg) from exc
    if status < _HTTP_OK or status >= _HTTP_BAD_REQUEST:
        msg = (
            f"LocalStack health check failed status={status} at {endpoint}\n"
            "  Start or recreate: just localstack-up"
        )
        raise CliError(msg)


def dump_job(job: dict[str, Any] | None) -> str:
    if job is None:
        return "<no response>"
    return json.dumps(job, indent=2, default=str)


def http_json(
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
        raise CliError(msg) from exc
    except urllib.error.URLError as exc:
        msg = f"HTTP {method} {url} failed: {exc}"
        raise CliError(msg) from exc

    try:
        payload: object = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        msg = f"HTTP {method} {url} returned non-JSON (status={status}): {exc}\n{raw}"
        raise CliError(msg) from exc

    if not isinstance(payload, dict):
        msg = f"HTTP {method} {url} JSON was not an object (status={status}): {payload!r}"
        raise CliError(msg)
    return status, cast("dict[str, Any]", payload)


def http_put(
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
        raise CliError(msg) from exc
    except TimeoutError as exc:
        msg = f"HTTP PUT upload timed out after {timeout}s url={url}: {exc}"
        raise CliError(msg) from exc
    except urllib.error.URLError as exc:
        msg = f"HTTP PUT upload failed url={url}: {exc}"
        raise CliError(msg) from exc


def require_str(value: object, *, field: str, context: object) -> str:
    if not isinstance(value, str) or not value:
        msg = f"missing or empty {field}: {context}"
        raise CliError(msg)
    return value
