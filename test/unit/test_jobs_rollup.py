"""Unit tests for job status rollup and DynamoDB size-map keys."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from thumbnail_api.jobs import compute_job_status, size_key

if TYPE_CHECKING:
    from thumbnail_api.jobs.types import JobStatus, SizeState, SizeStatus


def _entry(status: SizeStatus, *, output_key: str | None = None) -> SizeState:
    return {"status": status, "output_key": output_key}


def test_compute_job_status_rejects_empty_sizes() -> None:
    with pytest.raises(ValueError, match="sizes must not be empty"):
        compute_job_status({})


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (("complete", "complete", "complete"), "complete"),
        (("complete",), "complete"),
        (("failed", "complete", "pending"), "failed"),
        (("complete", "failed"), "failed"),
        (("failed",), "failed"),
        (("complete", "processing", "pending"), "processing"),
        (("pending", "pending"), "processing"),
        (("processing",), "processing"),
        (("complete", "pending"), "processing"),
    ],
)
def test_compute_job_status_from_size_map(
    statuses: tuple[SizeStatus, ...],
    expected: JobStatus,
) -> None:
    sizes = {
        str(128 * (index + 1)): _entry(
            status,
            output_key=f"thumbnails/job/{128 * (index + 1)}.jpg" if status == "complete" else None,
        )
        for index, status in enumerate(statuses)
    }
    assert compute_job_status(sizes) == expected


@pytest.mark.parametrize("size", [1, 128, 256, 512])
def test_size_key_from_positive_int(size: int) -> None:
    assert size_key(size) == str(size)


@pytest.mark.parametrize("size", [0, -1, -256])
def test_size_key_rejects_non_positive_int(size: int) -> None:
    with pytest.raises(ValueError, match="size must be a positive integer"):
        size_key(size)


@pytest.mark.parametrize("size", ["1", "128", "256", "512", "0"])
def test_size_key_from_canonical_digit_string(size: str) -> None:
    assert size_key(size) == size


@pytest.mark.parametrize(
    "size",
    ["", "abc", "12a", "0256", "00", "256.0", "+256", " 128", "128 "],
)
def test_size_key_rejects_invalid_string(size: str) -> None:
    with pytest.raises(ValueError, match="invalid size key"):
        size_key(size)
