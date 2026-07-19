"""S3 upload/download helpers for create-job (presign) and workers (get/put)."""

from typing import NamedTuple

from botocore.client import BaseClient

from .keys import (
    OUTPUT_CONTENT_TYPE,
    build_input_key,
    build_output_key,
    validate_upload_content_type,
)

DEFAULT_PRESIGN_EXPIRES_IN = 3600


class PresignedUpload(NamedTuple):
    """Presigned PUT target for a job's original upload."""

    url: str
    key: str


class InputObject(NamedTuple):
    """Bytes and metadata for a job's original input object."""

    key: str
    body: bytes
    content_type: str


def generate_presigned_put_url(
    s3_client: BaseClient,
    *,
    bucket: str,
    job_id: str,
    content_type: str,
    expires_in: int = DEFAULT_PRESIGN_EXPIRES_IN,
) -> PresignedUpload:
    """Build a path-style presigned PUT URL for the job's input object.

    The client must send the same ``Content-Type`` on the PUT (signed header).
    With a LocalStack path-style S3 client, the URL host matches the configured
    endpoint (usable with ``curl`` against LocalStack).
    """
    if not bucket:
        msg = "bucket must be a non-empty string"
        raise ValueError(msg)
    if expires_in <= 0:
        msg = "expires_in must be a positive integer"
        raise ValueError(msg)

    validated_content_type = validate_upload_content_type(content_type)
    key = build_input_key(job_id)
    url = s3_client.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": bucket,
            "Key": key,
            "ContentType": validated_content_type,
        },
        ExpiresIn=expires_in,
        HttpMethod="PUT",
    )
    return PresignedUpload(url=url, key=key)


def get_input_object(
    s3_client: BaseClient,
    *,
    bucket: str,
    job_id: str,
) -> InputObject:
    """Fetch the original upload for a job from the input bucket."""
    if not bucket:
        msg = "bucket must be a non-empty string"
        raise ValueError(msg)

    key = build_input_key(job_id)
    response = s3_client.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read()
    content_type = response.get("ContentType") or ""
    return InputObject(key=key, body=body, content_type=content_type)


def put_output_object(
    s3_client: BaseClient,
    *,
    bucket: str,
    job_id: str,
    size: int,
    body: bytes,
) -> str:
    """Write a JPEG thumbnail to the output bucket; return the object key."""
    if not bucket:
        msg = "bucket must be a non-empty string"
        raise ValueError(msg)

    key = build_output_key(job_id, size)
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=OUTPUT_CONTENT_TYPE,
    )
    return key
