from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

JobStatus = Literal["pending", "processing", "complete", "failed"]
SizeStatus = Literal["pending", "processing", "complete", "failed"]

JOB_STATUSES: frozenset[str] = frozenset({"pending", "processing", "complete", "failed"})
SIZE_STATUSES: frozenset[str] = frozenset({"pending", "processing", "complete", "failed"})
TERMINAL_JOB_STATUSES: frozenset[str] = frozenset({"complete", "failed"})
TERMINAL_SIZE_STATUSES: frozenset[str] = frozenset({"complete", "failed"})


class SizeState(TypedDict):
    status: SizeStatus
    output_key: str | None
    error: NotRequired[str]


class JobRecord(TypedDict):
    job_id: str
    status: JobStatus
    input_key: str
    sizes: dict[str, SizeState]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class SizeUpdateResult:
    """Outcome of a per-size status write.

    ``applied`` is True only when this call changed the size map.
    ``job`` is the item after the attempt (None if the job does not exist).
    """

    applied: bool
    job: JobRecord | None


@dataclass(frozen=True, slots=True)
class JobStatusUpdateResult:
    """Outcome of an overall job status write."""

    applied: bool
    job: JobRecord | None


class JobAlreadyExistsError(Exception):
    """Raised when ``put_pending_job`` would overwrite an existing ``job_id``."""


class JobNotFoundError(Exception):
    """Raised when a mutating helper targets a missing job."""
