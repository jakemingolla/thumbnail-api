"""``python -m thumbnail_api.cli <command> …`` entrypoint."""

from __future__ import annotations

import sys

from thumbnail_api.cli.download_job import main as download_job_main
from thumbnail_api.cli.style import eprint
from thumbnail_api.cli.upload_watch import main as upload_watch_main


def _usage(*, error: bool) -> int:
    print("usage: python -m thumbnail_api.cli <command> …")
    print()
    print("commands:")
    print("  upload-watch   Create job → PUT image → poll until terminal")
    print("  download-job   GET job → write {size}.jpg from output bucket")
    print()
    print("examples:")
    print("  python -m thumbnail_api.cli upload-watch ./photo.jpg")
    print("  python -m thumbnail_api.cli download-job <job_id>")
    return 2 if error else 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return _usage(error=True)
    if args[0] in {"-h", "--help"}:
        return _usage(error=False)

    command = args[0]
    if command == "upload-watch":
        return upload_watch_main(args[1:])
    if command == "download-job":
        return download_job_main(args[1:])

    eprint(f"error: unknown command {command!r}")
    eprint("known commands: download-job, upload-watch")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
