from datetime import UTC, datetime
from typing import Any, cast

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from .types import JobRecord

_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp with millisecond precision and a ``Z`` suffix."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def to_item(record: JobRecord) -> dict[str, Any]:
    """Serialize a job record to a low-level DynamoDB item."""
    # TypeSerializer is untyped; AttributeValue maps are dict[str, Any] by nature.
    return cast("dict[str, Any]", _SERIALIZER.serialize(record)["M"])


def from_item(item: dict[str, Any]) -> JobRecord:
    """Deserialize a low-level DynamoDB item into a typed job record."""
    # We only read items this package wrote (or that match the job-state-machine shape).
    return cast("JobRecord", _DESERIALIZER.deserialize({"M": item}))


def serialize_value(value: object) -> dict[str, Any]:
    """Serialize a Python value to a DynamoDB AttributeValue."""
    return _SERIALIZER.serialize(value)
