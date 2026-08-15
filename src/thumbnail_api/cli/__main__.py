"""``python -m thumbnail_api.cli <command> …`` entrypoint."""

from __future__ import annotations

import sys

from thumbnail_api.cli.admin_status import main as admin_status_main
from thumbnail_api.cli.bench import main as bench_main
from thumbnail_api.cli.download_job import main as download_job_main
from thumbnail_api.cli.style import eprint
from thumbnail_api.cli.upload import main as upload_main

_COMMANDS = {
    "admin-status": admin_status_main,
    "bench": bench_main,
    "download-job": download_job_main,
    "upload": upload_main,
}


def _usage(*, error: bool) -> int:
    print("usage: python -m thumbnail_api.cli <command> …")
    print()
    print("commands:")
    print("  admin-status   SQS / DynamoDB / S3 snapshot with ASCII graphs")
    print("  bench          Time POST / PUT / poll-to-complete across N runs")
    print("  upload         Create job → PUT image (add --watch to poll)")
    print("  download-job   GET job → write {size}.jpg from output bucket")
    print()
    print("examples:")
    print("  python -m thumbnail_api.cli admin-status")
    print("  python -m thumbnail_api.cli admin-status --watch")
    print("  python -m thumbnail_api.cli bench")
    print("  python -m thumbnail_api.cli bench ./photo.jpg --runs 5 --warmup 1")
    print("  python -m thumbnail_api.cli upload ./photo.jpg")
    print("  python -m thumbnail_api.cli upload ./photo.jpg --watch")
    print("  python -m thumbnail_api.cli download-job <job_id>")
    return 2 if error else 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        return _usage(error=not args)

    handler = _COMMANDS.get(args[0])
    if handler is None:
        eprint(f"error: unknown command {args[0]!r}")
        eprint(f"known commands: {', '.join(_COMMANDS)}")
        return 2
    return handler(args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
