from .types import JobStatus, SizeState


def compute_job_status(sizes: dict[str, SizeState]) -> JobStatus:
    """Return overall status from per-size statuses after the job has left ``pending``.

    Rules (see ``docs/specification/job-state-machine.md``):

    1. Any size ``failed`` → ``failed``
    2. Every size ``complete`` → ``complete``
    3. Otherwise → ``processing``
    """
    if not sizes:
        msg = "sizes must not be empty"
        raise ValueError(msg)

    statuses = [entry["status"] for entry in sizes.values()]
    if any(status == "failed" for status in statuses):
        return "failed"
    if all(status == "complete" for status in statuses):
        return "complete"
    return "processing"


def size_key(size: int | str) -> str:
    """Return the DynamoDB map key for a configured size (decimal string)."""
    if isinstance(size, int):
        if size <= 0:
            msg = "size must be a positive integer"
            raise ValueError(msg)
        return str(size)
    if not size or not size.isdigit() or size != str(int(size)):
        msg = f"invalid size key: {size!r}"
        raise ValueError(msg)
    return size
