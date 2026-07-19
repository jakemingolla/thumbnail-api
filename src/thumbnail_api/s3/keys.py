"""S3 object key builders matching docs/specification/s3-keys.md."""

from __future__ import annotations

ALLOWED_UPLOAD_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)
OUTPUT_CONTENT_TYPE = "image/jpeg"

_INPUT_KEY_PREFIX = "uploads/"
_INPUT_KEY_SUFFIX = "/original"


def _validate_job_id(job_id: str) -> None:
    if not job_id:
        msg = "job_id must be a non-empty string"
        raise ValueError(msg)
    if "/" in job_id:
        msg = "job_id must not contain '/'"
        raise ValueError(msg)


def _validate_size(size: int) -> str:
    # bool is a subclass of int; reject it explicitly.
    if isinstance(size, bool) or size <= 0:
        msg = "size must be a positive integer"
        raise ValueError(msg)
    token = str(size)
    if "/" in token or "." in token:
        msg = "size must not contain '/' or '.'"
        raise ValueError(msg)
    return token


def build_input_key(job_id: str) -> str:
    """Return the input object key for a job: ``uploads/{job_id}/original``."""
    _validate_job_id(job_id)
    return f"{_INPUT_KEY_PREFIX}{job_id}{_INPUT_KEY_SUFFIX}"


def parse_input_key(key: str) -> str:
    """Extract ``job_id`` from an input key, or raise ``ValueError`` if invalid.

    Accepts only the normative pattern ``uploads/{job_id}/original``.
    """
    if not key.startswith(_INPUT_KEY_PREFIX) or not key.endswith(_INPUT_KEY_SUFFIX):
        msg = "key must match uploads/{job_id}/original"
        raise ValueError(msg)
    job_id = key[len(_INPUT_KEY_PREFIX) : -len(_INPUT_KEY_SUFFIX)]
    _validate_job_id(job_id)
    return job_id


def build_output_key(job_id: str, size: int) -> str:
    """Return the output object key: ``thumbnails/{job_id}/{size}.jpg``."""
    _validate_job_id(job_id)
    size_token = _validate_size(size)
    return f"thumbnails/{job_id}/{size_token}.jpg"


def validate_upload_content_type(content_type: str) -> str:
    """Return ``content_type`` if it is an allowed upload Content-Type."""
    if content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
        allowed = ", ".join(sorted(ALLOWED_UPLOAD_CONTENT_TYPES))
        msg = f"content_type must be one of: {allowed}"
        raise ValueError(msg)
    return content_type
