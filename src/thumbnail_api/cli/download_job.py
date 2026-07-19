"""Download completed thumbnail objects for a job to ``{size}.jpg`` files."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Protocol, cast

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from thumbnail_api.cli.local import (
    CliError,
    dump_job,
    http_json,
    resolve_api_base,
    resolve_localstack_endpoint,
    resolve_output_bucket,
)
from thumbnail_api.cli.style import (
    bold,
    dim,
    eprint,
    green,
    heading,
    human_bytes,
    kv,
    paint,
    size_sort_key,
    status_color,
)

_HTTP_OK = 200
_PATH_STYLE_S3 = BotoConfig(
    signature_version="s3v4",
    s3={"addressing_style": "path"},
)


class _S3GetObject(Protocol):
    def get_object(self, **kwargs: object) -> dict[str, Any]: ...


def _sizes_map(job: dict[str, Any]) -> dict[str, Any] | None:
    sizes_raw = job.get("sizes")
    if not isinstance(sizes_raw, dict):
        return None
    return cast("dict[str, Any]", sizes_raw)


def _complete_outputs(job: dict[str, Any]) -> list[tuple[str, str]]:
    sizes = _sizes_map(job)
    if sizes is None:
        msg = f"job.sizes must be an object:\n{dump_job(job)}"
        raise CliError(msg)

    outputs: list[tuple[str, str]] = []
    for size, entry in sizes.items():
        label = str(size)
        if not isinstance(entry, dict):
            continue
        entry_map = cast("dict[str, Any]", entry)
        if entry_map.get("status") != "complete":
            continue
        output_key = entry_map.get("output_key")
        if not isinstance(output_key, str) or not output_key:
            msg = f"size {label} is complete but missing output_key:\n{dump_job(job)}"
            raise CliError(msg)
        outputs.append((label, output_key))

    outputs.sort(key=lambda item: size_sort_key(item[0]))
    return outputs


def _skipped_sizes(job: dict[str, Any], outputs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    sizes = _sizes_map(job)
    if sizes is None:
        return []
    complete = {size for size, _ in outputs}
    skipped: list[tuple[str, str]] = []
    for size, entry in sizes.items():
        label = str(size)
        if label in complete:
            continue
        status = "?"
        if isinstance(entry, dict):
            entry_map = cast("dict[str, Any]", entry)
            raw_status = entry_map.get("status")
            if isinstance(raw_status, str):
                status = raw_status
            elif raw_status is not None:
                status = repr(raw_status)
        skipped.append((label, status))
    skipped.sort(key=lambda item: size_sort_key(item[0]))
    return skipped


def _s3_client(localstack_endpoint: str) -> _S3GetObject:
    return cast(
        "_S3GetObject",
        boto3.client(
            "s3",
            config=_PATH_STYLE_S3,
            endpoint_url=localstack_endpoint,
            region_name=os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "us-east-1",
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
        ),
    )


def _download_object(
    *,
    s3: _S3GetObject,
    bucket: str,
    key: str,
    dest: Path,
) -> int:
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        msg = f"S3 get_object failed for s3://{bucket}/{key}: {exc}"
        raise CliError(msg) from exc
    body_obj = response.get("Body")
    read = getattr(body_obj, "read", None)
    if not callable(read):
        msg = f"unexpected S3 body for s3://{bucket}/{key}: {type(body_obj)}"
        raise CliError(msg)
    body = read()
    if not isinstance(body, (bytes, bytearray)):
        msg = f"unexpected S3 body type for s3://{bucket}/{key}: {type(body)}"
        raise CliError(msg)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(bytes(body))
    return len(body)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="download-job",
        description=(
            "Fetch a job from the API and download each complete thumbnail "
            "to {size}.jpg (via LocalStack S3)."
        ),
    )
    parser.add_argument("job_id", help="Job UUID from POST /jobs or upload-watch")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(),
        help="Directory for {size}.jpg files (default: current directory)",
    )
    parser.add_argument(
        "--api-base",
        default=None,
        help="API base URL (default: $API_BASE or terraform output api_base_url)",
    )
    parser.add_argument(
        "--localstack-endpoint",
        default=None,
        help=("Host-reachable LocalStack edge (default: $LOCALSTACK_ENDPOINT or terraform output)"),
    )
    parser.add_argument(
        "--output-bucket",
        default=None,
        help=(
            "Output bucket name (default: $OUTPUT_BUCKET or terraform output output_bucket_name)"
        ),
    )
    return parser.parse_args(argv)


def _run(args: argparse.Namespace) -> None:
    job_id: str = args.job_id.strip()
    if not job_id:
        msg = "job_id must be non-empty"
        raise CliError(msg)

    api_base = resolve_api_base(args.api_base)
    localstack_endpoint = resolve_localstack_endpoint(args.localstack_endpoint)
    output_bucket = resolve_output_bucket(args.output_bucket)
    out_dir: Path = args.out_dir

    print(heading("download-job"))
    print(kv("api", api_base))
    print(kv("job", job_id))
    print(kv("bucket", output_bucket))
    print(kv("out", str(out_dir.resolve())))
    print()

    status_code, job = http_json("GET", f"{api_base}/jobs/{job_id}")
    if status_code != _HTTP_OK:
        msg = f"GET /jobs/{job_id} returned HTTP {status_code}:\n{dump_job(job)}"
        raise CliError(msg)

    overall = job.get("status")
    print(f"  {green('✓')} {bold('fetched')}   {job_id}")
    print(kv("status", status_color(overall)))

    outputs = _complete_outputs(job)
    if not outputs:
        msg = (
            f"no complete sizes with output_key for job {job_id} "
            f"(status={overall}).\n{dump_job(job)}"
        )
        raise CliError(msg)

    print()
    size_width = max(len(size) for size, _ in outputs)
    file_width = max(len(f"{size}.jpg") for size, _ in outputs)
    print(
        f"  {dim('size'.ljust(size_width))}  "
        f"{dim('file'.ljust(file_width))}  "
        f"{dim('bytes'.rjust(9))}  "
        f"{dim('key')}"
    )
    print(f"  {dim('─' * size_width)}  {dim('─' * file_width)}  {dim('─' * 9)}  {dim('─' * 12)}")

    s3 = _s3_client(localstack_endpoint)
    written: list[Path] = []
    for size, output_key in outputs:
        dest = out_dir / f"{size}.jpg"
        nbytes = _download_object(
            s3=s3,
            bucket=output_bucket,
            key=output_key,
            dest=dest,
        )
        print(
            f"  {size.rjust(size_width)}  "
            f"{green(f'{size}.jpg'.ljust(file_width))}  "
            f"{human_bytes(nbytes).rjust(9)}  "
            f"{dim(output_key)}"
        )
        written.append(dest)

    skipped = _skipped_sizes(job, outputs)
    if skipped:
        print()
        print(kv("skipped", f"{len(skipped)} non-complete size(s)"))
        for size, status in skipped:
            print(f"  {dim('size')} {size.rjust(size_width)}  {status_color(status)}")

    print()
    print(f"  {green('OK')}  wrote {bold(str(len(written)))} file(s) under {out_dir.resolve()}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        _run(args)
    except CliError as exc:
        eprint(paint(f"error: {exc}", "\033[31m", stream=sys.stderr))
        return 1
    return 0
