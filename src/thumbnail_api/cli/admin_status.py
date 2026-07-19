"""Local-stack admin snapshot: SQS / DynamoDB / S3 with ASCII graphs."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol, cast

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from thumbnail_api.cli.local import (
    CliError,
    aws_region,
    ensure_localstack_healthy,
    resolve_input_bucket,
    resolve_jobs_table,
    resolve_localstack_endpoint,
    resolve_output_bucket,
    resolve_work_dlq_url,
    resolve_work_queue_url,
)
from thumbnail_api.cli.style import (
    ascii_bar,
    bold,
    cyan,
    dim,
    eprint,
    green,
    heading,
    human_bytes,
    red,
    sparkline,
)

_DEFAULT_INTERVAL_SECONDS = 2.0
_DEFAULT_SAMPLE_WINDOW = 24
_BAR_WIDTH = 10
_PATH_STYLE_S3 = BotoConfig(
    signature_version="s3v4",
    s3={"addressing_style": "path"},
)
_SQS_ATTRS = (
    "ApproximateNumberOfMessages",
    "ApproximateNumberOfMessagesNotVisible",
)
_INPUT_PREFIX = "uploads/"
_OUTPUT_PREFIX = "thumbnails/"
_CLEAR_SCREEN = "\033[H\033[J"


class _SqsClient(Protocol):
    def get_queue_attributes(self, **kwargs: object) -> dict[str, Any]: ...


class _DynamoDbClient(Protocol):
    def scan(self, **kwargs: object) -> dict[str, Any]: ...


class _S3Client(Protocol):
    def list_objects_v2(self, **kwargs: object) -> dict[str, Any]: ...


@dataclass(frozen=True)
class QueueDepth:
    visible: int
    in_flight: int


@dataclass(frozen=True)
class PrefixStats:
    count: int
    size_bytes: int


@dataclass(frozen=True)
class BucketStats:
    name: str
    primary_prefix: str
    primary: PrefixStats
    other: PrefixStats


def bucket_total_count(bucket: BucketStats) -> int:
    return bucket.primary.count + bucket.other.count


def bucket_total_bytes(bucket: BucketStats) -> int:
    return bucket.primary.size_bytes + bucket.other.size_bytes


@dataclass(frozen=True)
class StackTargets:
    endpoint: str
    work_queue_url: str
    dlq_url: str
    jobs_table: str
    input_bucket: str
    output_bucket: str


@dataclass(frozen=True)
class Snapshot:
    endpoint: str
    region: str
    work_queue_url: str
    dlq_url: str
    work: QueueDepth
    dlq: QueueDepth
    jobs_table: str
    job_count: int
    input_bucket: BucketStats
    output_bucket: BucketStats


@dataclass
class History:
    """Rolling sample window for watch-mode sparklines."""

    window: int
    work_visible: deque[int]
    work_in_flight: deque[int]
    dlq_visible: deque[int]
    job_count: deque[int]

    @classmethod
    def create(cls, window: int) -> History:
        """Allocate empty deques with a fixed sample window."""
        return cls(
            window=window,
            work_visible=deque(maxlen=window),
            work_in_flight=deque(maxlen=window),
            dlq_visible=deque(maxlen=window),
            job_count=deque(maxlen=window),
        )

    def append(self, snapshot: Snapshot) -> None:
        """Record one poll into the rolling window."""
        self.work_visible.append(snapshot.work.visible)
        self.work_in_flight.append(snapshot.work.in_flight)
        self.dlq_visible.append(snapshot.dlq.visible)
        self.job_count.append(snapshot.job_count)


def _int_attr(attrs: dict[str, str], key: str) -> int:
    raw = attrs.get(key, "0")
    try:
        return int(raw)
    except ValueError:
        return 0


def _queue_depth(sqs: _SqsClient, queue_url: str) -> QueueDepth:
    try:
        response = sqs.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=list(_SQS_ATTRS),
        )
    except (ClientError, BotoCoreError) as exc:
        msg = f"SQS get-queue-attributes failed for {queue_url}: {exc}"
        raise CliError(msg) from exc
    attrs_raw = response.get("Attributes", {})
    if not isinstance(attrs_raw, dict):
        msg = f"SQS attributes missing for {queue_url}"
        raise CliError(msg)
    attrs = cast("dict[str, str]", attrs_raw)
    return QueueDepth(
        visible=_int_attr(attrs, "ApproximateNumberOfMessages"),
        in_flight=_int_attr(attrs, "ApproximateNumberOfMessagesNotVisible"),
    )


def _job_count(dynamodb: _DynamoDbClient, table_name: str) -> int:
    total = 0
    start_key: dict[str, Any] | None = None
    try:
        while True:
            kwargs: dict[str, object] = {
                "TableName": table_name,
                "Select": "COUNT",
            }
            if start_key is not None:
                kwargs["ExclusiveStartKey"] = start_key
            response = dynamodb.scan(**kwargs)
            total += int(response.get("Count", 0))
            next_key = response.get("LastEvaluatedKey")
            if not next_key:
                break
            if not isinstance(next_key, dict):
                break
            start_key = cast("dict[str, Any]", next_key)
    except (ClientError, BotoCoreError) as exc:
        msg = f"DynamoDB scan COUNT failed for table {table_name}: {exc}"
        raise CliError(msg) from exc
    return total


def _empty_prefix() -> PrefixStats:
    return PrefixStats(count=0, size_bytes=0)


def _bucket_stats(s3: _S3Client, *, bucket: str, primary_prefix: str) -> BucketStats:
    primary = _empty_prefix()
    other = _empty_prefix()
    token: str | None = None
    try:
        while True:
            kwargs: dict[str, object] = {"Bucket": bucket}
            if token is not None:
                kwargs["ContinuationToken"] = token
            response = s3.list_objects_v2(**kwargs)
            contents = response.get("Contents") or []
            if isinstance(contents, list):
                for item in contents:
                    if not isinstance(item, dict):
                        continue
                    obj = cast("dict[str, Any]", item)
                    key = obj.get("Key")
                    size_raw = obj.get("Size", 0)
                    size = int(size_raw) if isinstance(size_raw, (int, float)) else 0
                    if isinstance(key, str) and key.startswith(primary_prefix):
                        primary = PrefixStats(
                            count=primary.count + 1,
                            size_bytes=primary.size_bytes + size,
                        )
                    else:
                        other = PrefixStats(
                            count=other.count + 1,
                            size_bytes=other.size_bytes + size,
                        )
            if not response.get("IsTruncated"):
                break
            next_token = response.get("NextContinuationToken")
            if not isinstance(next_token, str) or not next_token:
                break
            token = next_token
    except (ClientError, BotoCoreError) as exc:
        msg = f"S3 list-objects failed for bucket {bucket}: {exc}"
        raise CliError(msg) from exc
    return BucketStats(
        name=bucket,
        primary_prefix=primary_prefix,
        primary=primary,
        other=other,
    )


def _boto_kwargs(endpoint: str) -> dict[str, object]:
    return {
        "endpoint_url": endpoint,
        "region_name": aws_region(),
        "aws_access_key_id": os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        "aws_secret_access_key": os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    }


@dataclass(frozen=True)
class AdminClients:
    """Injectable AWS clients (real boto3 or unit-test fakes)."""

    sqs: _SqsClient
    dynamodb: _DynamoDbClient
    s3: _S3Client


def _default_clients(endpoint: str) -> AdminClients:
    kwargs = _boto_kwargs(endpoint)
    return AdminClients(
        sqs=cast("_SqsClient", boto3.client("sqs", **kwargs)),
        dynamodb=cast("_DynamoDbClient", boto3.client("dynamodb", **kwargs)),
        s3=cast("_S3Client", boto3.client("s3", config=_PATH_STYLE_S3, **kwargs)),
    )


def collect_snapshot(
    targets: StackTargets,
    clients: AdminClients | None = None,
) -> Snapshot:
    resolved = clients or _default_clients(targets.endpoint)
    return Snapshot(
        endpoint=targets.endpoint,
        region=aws_region(),
        work_queue_url=targets.work_queue_url,
        dlq_url=targets.dlq_url,
        work=_queue_depth(resolved.sqs, targets.work_queue_url),
        dlq=_queue_depth(resolved.sqs, targets.dlq_url),
        jobs_table=targets.jobs_table,
        job_count=_job_count(resolved.dynamodb, targets.jobs_table),
        input_bucket=_bucket_stats(
            resolved.s3,
            bucket=targets.input_bucket,
            primary_prefix=_INPUT_PREFIX,
        ),
        output_bucket=_bucket_stats(
            resolved.s3,
            bucket=targets.output_bucket,
            primary_prefix=_OUTPUT_PREFIX,
        ),
    )


def _bar(value: int, scale: int) -> str:
    graph = ascii_bar(value, width=_BAR_WIDTH, scale=scale)
    return cyan(graph)


def _spark(samples: list[int], *, window: int) -> str:
    return cyan(sparkline(samples, width=window))


def _dlq_badge(visible: int) -> str:
    if visible > 0:
        return red("[WARN]")
    return green("[ok]")


def _prefix_row(label: str, stats: PrefixStats, *, scale: int) -> str:
    return (
        f"  {dim(label.ljust(12))}"
        f"{_bar(stats.count, scale)}  "
        f"{stats.count:>3} objs · {human_bytes(stats.size_bytes)}"
    )


def _bucket_lines(bucket: BucketStats) -> list[str]:
    total_count = bucket_total_count(bucket)
    scale = max(1, total_count, bucket.primary.count, bucket.other.count)
    return [
        (
            f"{heading('S3')}  {bucket.name}  "
            f"{total_count} objs · {human_bytes(bucket_total_bytes(bucket))}"
        ),
        _prefix_row(bucket.primary_prefix, bucket.primary, scale=scale),
        _prefix_row("other", bucket.other, scale=scale),
    ]


def format_report(
    snapshot: Snapshot,
    *,
    history: History | None = None,
    interval_seconds: float | None = None,
) -> str:
    """Render a scannable admin frame (ANSI when color is enabled)."""
    work_scale = max(
        1,
        snapshot.work.visible,
        snapshot.work.in_flight,
        *(history.work_visible if history is not None else ()),
        *(history.work_in_flight if history is not None else ()),
    )
    dlq_scale = max(
        1,
        snapshot.dlq.visible,
        *(history.dlq_visible if history is not None else ()),
    )
    jobs_scale = max(
        1,
        snapshot.job_count,
        *(history.job_count if history is not None else ()),
    )

    lines: list[str] = [
        (
            f"{bold('Admin status')}  "
            f"endpoint={dim(snapshot.endpoint)}  "
            f"region={dim(snapshot.region)}"
        ),
    ]
    if history is not None and interval_seconds is not None:
        lines.append(
            dim(
                f"watch  every {interval_seconds:g}s · "
                f"last {history.window} samples · Ctrl+C to stop"
            )
        )
    lines.append("")
    lines.append(heading("SQS"))

    if history is not None:
        lines.extend(
            [
                (
                    f"  {dim('work'.ljust(10))}visible   "
                    f"{snapshot.work.visible:>3}  {_bar(snapshot.work.visible, work_scale)}  "
                    f"{_spark(list(history.work_visible), window=history.window)}"
                ),
                (
                    f"  {'':10}in-flight "
                    f"{snapshot.work.in_flight:>3}  "
                    f"{_bar(snapshot.work.in_flight, work_scale)}  "
                    f"{_spark(list(history.work_in_flight), window=history.window)}"
                ),
                (
                    f"  {dim('work-dlq'.ljust(10))}visible   "
                    f"{snapshot.dlq.visible:>3}  {_bar(snapshot.dlq.visible, dlq_scale)}  "
                    f"{_spark(list(history.dlq_visible), window=history.window)}  "
                    f"{_dlq_badge(snapshot.dlq.visible)}"
                ),
            ]
        )
    else:
        lines.extend(
            [
                (
                    f"  {dim('work'.ljust(10))}visible "
                    f"{_bar(snapshot.work.visible, work_scale)}  "
                    f"{snapshot.work.visible:>3}    "
                    f"in-flight {_bar(snapshot.work.in_flight, work_scale)}  "
                    f"{snapshot.work.in_flight:>3}"
                ),
                (
                    f"  {dim('work-dlq'.ljust(10))}visible "
                    f"{_bar(snapshot.dlq.visible, dlq_scale)}  "
                    f"{snapshot.dlq.visible:>3}    "
                    f"{_dlq_badge(snapshot.dlq.visible)}"
                ),
            ]
        )

    lines.append("")
    lines.append(f"{heading('DynamoDB')}  {snapshot.jobs_table}")
    if history is not None:
        lines.append(
            f"  {dim('items'.ljust(10))}"
            f"{snapshot.job_count:>3}  {_bar(snapshot.job_count, jobs_scale)}  "
            f"{_spark(list(history.job_count), window=history.window)}  "
            f"{dim('(scan count)')}"
        )
    else:
        lines.append(
            f"  {dim('items'.ljust(10))}"
            f"{_bar(snapshot.job_count, jobs_scale)}  "
            f"{snapshot.job_count:>3}    {dim('(scan count)')}"
        )

    lines.append("")
    lines.extend(_bucket_lines(snapshot.input_bucket))
    lines.append("")
    lines.extend(_bucket_lines(snapshot.output_bucket))
    return "\n".join(lines)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="thumbnail_api.cli admin-status",
        description=(
            "Show LocalStack admin snapshot: SQS depths, DynamoDB job count, "
            "and S3 object counts/sizes with ASCII graphs."
        ),
    )
    parser.add_argument(
        "-w",
        "--watch",
        action="store_true",
        help="refresh continuously; track SQS/DynamoDB samples as sparklines",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=_DEFAULT_INTERVAL_SECONDS,
        metavar="SECONDS",
        help=f"watch poll interval (default {_DEFAULT_INTERVAL_SECONDS:g})",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=_DEFAULT_SAMPLE_WINDOW,
        metavar="N",
        help=f"sparkline sample window in watch mode (default {_DEFAULT_SAMPLE_WINDOW})",
    )
    parser.add_argument(
        "--endpoint",
        default=None,
        help="LocalStack edge URL (default: $LOCALSTACK_ENDPOINT or terraform output)",
    )
    return parser.parse_args(argv)


def _resolve_targets(endpoint_arg: str | None) -> StackTargets:
    endpoint = resolve_localstack_endpoint(endpoint_arg)
    ensure_localstack_healthy(endpoint)
    return StackTargets(
        endpoint=endpoint,
        work_queue_url=resolve_work_queue_url(),
        dlq_url=resolve_work_dlq_url(),
        jobs_table=resolve_jobs_table(),
        input_bucket=resolve_input_bucket(),
        output_bucket=resolve_output_bucket(None),
    )


def _print_frame(text: str, *, clear: bool) -> None:
    if clear and sys.stdout.isatty():
        sys.stdout.write(_CLEAR_SCREEN)
    print(text)
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.interval <= 0:
        eprint("error: --interval must be > 0")
        return 2
    if args.samples <= 0:
        eprint("error: --samples must be > 0")
        return 2

    try:
        targets = _resolve_targets(args.endpoint)
    except CliError as exc:
        eprint(f"error: {exc}")
        return 1

    history = History.create(args.samples) if args.watch else None

    try:
        while True:
            try:
                snapshot = collect_snapshot(targets)
            except CliError as exc:
                eprint(f"error: {exc}")
                return 1

            if history is not None:
                history.append(snapshot)
                frame = format_report(
                    snapshot,
                    history=history,
                    interval_seconds=args.interval,
                )
                _print_frame(frame, clear=True)
                time.sleep(args.interval)
                continue

            print(format_report(snapshot))
            return 0
    except KeyboardInterrupt:
        if args.watch:
            print()
            print(dim("stopped"))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
