"""Unit tests for shared DynamoDB job helpers."""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING, Any, cast

import pytest
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from collections.abc import Callable

    from thumbnail_api.jobs.store import DynamoDBClient

from thumbnail_api.config.types import DEFAULT_THUMBNAIL_SIZES
from thumbnail_api.jobs import (
    JobAlreadyExistsError,
    JobNotFoundError,
    claim_size,
    complete_size,
    compute_job_status,
    fail_size,
    get_job,
    mark_job_processing,
    put_pending_job,
    size_key,
)
from thumbnail_api.jobs.serde import from_item, serialize_value, to_item
from thumbnail_api.jobs.types import JobRecord

_TABLE = "jobs"
_JOB_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_INPUT_KEY = f"uploads/{_JOB_ID}/original"
_NOW = "2026-07-18T22:00:00.000Z"
_LATER = "2026-07-18T22:00:05.000Z"
_DESERIALIZER = TypeDeserializer()


def _conditional_check_failed() -> ClientError:
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "failed"}},
        "UpdateItem",
    )


class FakeDynamoDB:
    """Minimal DynamoDB stand-in that honors the helpers' condition expressions."""

    def __init__(self) -> None:
        """Create an empty in-memory table."""
        self.items: dict[str, JobRecord] = {}

    def put_item(self, **kwargs: object) -> dict[str, Any]:
        """Store a new item, honoring ``attribute_not_exists(job_id)``."""
        record = from_item(cast("dict[str, Any]", kwargs["Item"]))
        condition = kwargs.get("ConditionExpression")
        if condition == "attribute_not_exists(job_id)" and record["job_id"] in self.items:
            raise _conditional_check_failed()
        self.items[record["job_id"]] = record
        return {}

    def get_item(self, **kwargs: object) -> dict[str, Any]:
        """Return a stored item by key."""
        key = cast("dict[str, Any]", kwargs["Key"])
        job_id = cast("str", _deserialize_attr(key["job_id"]))
        item = self.items.get(job_id)
        if item is None:
            return {}
        return {"Item": to_item(item)}

    def update_item(self, **kwargs: object) -> dict[str, Any]:
        """Apply a conditional update against the in-memory item."""
        key = cast("dict[str, Any]", kwargs["Key"])
        job_id = cast("str", _deserialize_attr(key["job_id"]))
        names = cast("dict[str, str]", kwargs["ExpressionAttributeNames"])
        raw_values = cast("dict[str, Any]", kwargs["ExpressionAttributeValues"])
        values = {token: _deserialize_attr(value) for token, value in raw_values.items()}
        item = self.items.get(job_id)
        condition = cast("str", kwargs["ConditionExpression"])

        if not _condition_holds(condition, item, names, values):
            raise _conditional_check_failed()

        assert item is not None
        updated = copy.deepcopy(item)
        _apply_update(cast("str", kwargs["UpdateExpression"]), updated, names, values)
        self.items[job_id] = updated

        if kwargs.get("ReturnValues") == "ALL_NEW":
            return {"Attributes": to_item(updated)}
        return {}


def _deserialize_attr(attr: dict[str, Any]) -> object:
    return _DESERIALIZER.deserialize(attr)


def _resolve_path(path: str, names: dict[str, str]) -> list[str]:
    return [names.get(token, token) for token in path.split(".")]


def _get_path(item: JobRecord, path: list[str]) -> object:
    current: object = item
    for part in path:
        if not isinstance(current, dict):
            raise KeyError(part)
        current = current[part]
    return current


def _set_path(item: JobRecord, path: list[str], value: object) -> None:
    current: dict[str, Any] = cast("dict[str, Any]", item)
    for part in path[:-1]:
        next_value = current[part]
        assert isinstance(next_value, dict)
        current = next_value
    current[path[-1]] = value


# DynamoDB expression paths use placeholders like #sizes.#size.#status
_PATH = r"((?:#[A-Za-z0-9_]+)(?:\.#[A-Za-z0-9_]+)*)"
_VALUE = r"(:[A-Za-z0-9_]+)"


def _condition_holds(
    expression: str,
    item: JobRecord | None,
    names: dict[str, str],
    values: dict[str, object],
) -> bool:
    if "attribute_exists(job_id)" in expression and item is None:
        return False
    if item is None:
        return False

    for match in re.finditer(rf"attribute_exists\({_PATH}\)", expression):
        try:
            _get_path(item, _resolve_path(match.group(1), names))
        except KeyError:
            return False

    in_match = re.search(rf"{_PATH} IN \({_VALUE}, {_VALUE}\)", expression)
    if in_match:
        actual = _get_path(item, _resolve_path(in_match.group(1), names))
        allowed = {values[in_match.group(2)], values[in_match.group(3)]}
        if actual not in allowed:
            return False

    for match in re.finditer(rf"{_PATH} = {_VALUE}", expression):
        path_expr, value_token = match.group(1), match.group(2)
        if in_match and path_expr == in_match.group(1):
            continue
        actual = _get_path(item, _resolve_path(path_expr, names))
        if actual != values[value_token]:
            return False

    return True


def _apply_update(
    expression: str,
    item: JobRecord,
    names: dict[str, str],
    values: dict[str, object],
) -> None:
    assert expression.startswith("SET ")
    assignments = expression[len("SET ") :].split(", ")
    for assignment in assignments:
        left, right = assignment.split(" = ", maxsplit=1)
        _set_path(item, _resolve_path(left.strip(), names), values[right.strip()])


@pytest.fixture
def db() -> FakeDynamoDB:
    return FakeDynamoDB()


@pytest.fixture
def client(db: FakeDynamoDB) -> DynamoDBClient:
    return db


@pytest.fixture
def freeze_time(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    def _freeze(value: str) -> None:
        monkeypatch.setattr("thumbnail_api.jobs.store.utc_now_iso", lambda: value)

    return _freeze


def test_size_key_and_rollup() -> None:
    assert size_key(256) == "256"
    assert size_key("512") == "512"
    assert (
        compute_job_status(
            {
                "128": {"status": "complete", "output_key": "a"},
                "256": {"status": "complete", "output_key": "b"},
                "512": {"status": "complete", "output_key": "c"},
            }
        )
        == "complete"
    )
    assert (
        compute_job_status(
            {
                "128": {"status": "complete", "output_key": "a"},
                "256": {"status": "failed", "output_key": None},
                "512": {"status": "pending", "output_key": None},
            }
        )
        == "failed"
    )
    assert (
        compute_job_status(
            {
                "128": {"status": "complete", "output_key": "a"},
                "256": {"status": "processing", "output_key": None},
                "512": {"status": "pending", "output_key": None},
            }
        )
        == "processing"
    )


def test_put_pending_job_and_get_job(
    client: DynamoDBClient,
    freeze_time: Callable[[str], None],
) -> None:
    freeze_time(_NOW)
    created = put_pending_job(
        client,
        _TABLE,
        job_id=_JOB_ID,
        input_key=_INPUT_KEY,
        sizes=DEFAULT_THUMBNAIL_SIZES,
    )

    assert created["status"] == "pending"
    assert created["created_at"] == _NOW
    assert set(created["sizes"]) == {"128", "256", "512"}
    assert all(size["status"] == "pending" for size in created["sizes"].values())
    assert all(size["output_key"] is None for size in created["sizes"].values())

    loaded = get_job(client, _TABLE, _JOB_ID)
    assert loaded == created


def test_put_pending_job_rejects_duplicate_job_id(
    client: DynamoDBClient,
    freeze_time: Callable[[str], None],
) -> None:
    freeze_time(_NOW)
    put_pending_job(
        client,
        _TABLE,
        job_id=_JOB_ID,
        input_key=_INPUT_KEY,
        sizes=DEFAULT_THUMBNAIL_SIZES,
    )

    freeze_time(_LATER)
    with pytest.raises(JobAlreadyExistsError):
        put_pending_job(
            client,
            _TABLE,
            job_id=_JOB_ID,
            input_key=_INPUT_KEY,
            sizes=DEFAULT_THUMBNAIL_SIZES,
        )

    loaded = get_job(client, _TABLE, _JOB_ID)
    assert loaded is not None
    assert loaded["updated_at"] == _NOW


def test_mark_job_processing_happy_path(
    client: DynamoDBClient,
    freeze_time: Callable[[str], None],
) -> None:
    freeze_time(_NOW)
    put_pending_job(
        client,
        _TABLE,
        job_id=_JOB_ID,
        input_key=_INPUT_KEY,
        sizes=DEFAULT_THUMBNAIL_SIZES,
    )

    freeze_time(_LATER)
    result = mark_job_processing(client, _TABLE, _JOB_ID)

    assert result.applied is True
    assert result.job is not None
    assert result.job["status"] == "processing"
    assert result.job["updated_at"] == _LATER


def test_mark_job_processing_idempotent_when_already_processing(
    client: DynamoDBClient,
    freeze_time: Callable[[str], None],
) -> None:
    freeze_time(_NOW)
    put_pending_job(
        client,
        _TABLE,
        job_id=_JOB_ID,
        input_key=_INPUT_KEY,
        sizes=DEFAULT_THUMBNAIL_SIZES,
    )
    freeze_time(_LATER)
    first = mark_job_processing(client, _TABLE, _JOB_ID)
    freeze_time("2026-07-18T22:00:06.000Z")
    second = mark_job_processing(client, _TABLE, _JOB_ID)

    assert first.applied is True
    assert second.applied is False
    assert second.job is not None
    assert second.job["status"] == "processing"
    assert second.job["updated_at"] == _LATER


def test_claim_complete_size_happy_path_and_rollup(
    client: DynamoDBClient,
    freeze_time: Callable[[str], None],
) -> None:
    freeze_time(_NOW)
    put_pending_job(
        client,
        _TABLE,
        job_id=_JOB_ID,
        input_key=_INPUT_KEY,
        sizes=DEFAULT_THUMBNAIL_SIZES,
    )
    freeze_time(_LATER)
    mark_job_processing(client, _TABLE, _JOB_ID)

    for size in DEFAULT_THUMBNAIL_SIZES:
        claim = claim_size(client, _TABLE, _JOB_ID, size)
        assert claim.applied is True
        assert claim.job is not None
        assert claim.job["sizes"][str(size)]["status"] == "processing"

        freeze_time("2026-07-18T22:00:10.000Z")
        output_key = f"thumbnails/{_JOB_ID}/{size}.jpg"
        done = complete_size(client, _TABLE, _JOB_ID, size, output_key)
        assert done.applied is True
        assert done.job is not None
        assert done.job["sizes"][str(size)]["status"] == "complete"
        assert done.job["sizes"][str(size)]["output_key"] == output_key

    final = get_job(client, _TABLE, _JOB_ID)
    assert final is not None
    assert final["status"] == "complete"


def test_complete_size_idempotent_when_already_complete(
    client: DynamoDBClient,
    freeze_time: Callable[[str], None],
) -> None:
    freeze_time(_NOW)
    put_pending_job(
        client,
        _TABLE,
        job_id=_JOB_ID,
        input_key=_INPUT_KEY,
        sizes=DEFAULT_THUMBNAIL_SIZES,
    )
    freeze_time(_LATER)
    mark_job_processing(client, _TABLE, _JOB_ID)
    claim_size(client, _TABLE, _JOB_ID, 128)
    output_key = f"thumbnails/{_JOB_ID}/128.jpg"
    first = complete_size(client, _TABLE, _JOB_ID, 128, output_key)
    freeze_time("2026-07-18T22:00:99.000Z")
    second = complete_size(
        client,
        _TABLE,
        _JOB_ID,
        128,
        f"thumbnails/{_JOB_ID}/128-other.jpg",
    )

    assert first.applied is True
    assert second.applied is False
    assert second.job is not None
    assert second.job["sizes"]["128"]["status"] == "complete"
    assert second.job["sizes"]["128"]["output_key"] == output_key


def test_complete_size_does_not_overwrite_failed(
    client: DynamoDBClient,
    freeze_time: Callable[[str], None],
) -> None:
    freeze_time(_NOW)
    put_pending_job(
        client,
        _TABLE,
        job_id=_JOB_ID,
        input_key=_INPUT_KEY,
        sizes=DEFAULT_THUMBNAIL_SIZES,
    )
    freeze_time(_LATER)
    mark_job_processing(client, _TABLE, _JOB_ID)
    failed = fail_size(client, _TABLE, _JOB_ID, 256, error="corrupt")
    assert failed.applied is True
    assert failed.job is not None
    assert failed.job["status"] == "failed"

    freeze_time("2026-07-18T22:00:20.000Z")
    again = complete_size(client, _TABLE, _JOB_ID, 256, f"thumbnails/{_JOB_ID}/256.jpg")
    assert again.applied is False
    assert again.job is not None
    assert again.job["sizes"]["256"]["status"] == "failed"
    assert again.job["sizes"]["256"]["output_key"] is None
    assert again.job["status"] == "failed"


def test_fail_size_idempotent_and_keeps_terminal_job(
    client: DynamoDBClient,
    freeze_time: Callable[[str], None],
) -> None:
    freeze_time(_NOW)
    put_pending_job(
        client,
        _TABLE,
        job_id=_JOB_ID,
        input_key=_INPUT_KEY,
        sizes=DEFAULT_THUMBNAIL_SIZES,
    )
    freeze_time(_LATER)
    mark_job_processing(client, _TABLE, _JOB_ID)
    first = fail_size(client, _TABLE, _JOB_ID, 512, error="boom")
    freeze_time("2026-07-18T22:01:00.000Z")
    second = fail_size(client, _TABLE, _JOB_ID, 512, error="other")

    assert first.applied is True
    assert second.applied is False
    assert second.job is not None
    assert second.job["sizes"]["512"]["status"] == "failed"
    assert second.job["sizes"]["512"].get("error") == "boom"
    assert second.job["status"] == "failed"


def test_claim_size_missing_job_raises(client: DynamoDBClient) -> None:
    with pytest.raises(JobNotFoundError):
        claim_size(client, _TABLE, _JOB_ID, 128)


def test_serde_roundtrip_preserves_null_output_key() -> None:
    record = JobRecord(
        job_id=_JOB_ID,
        status="pending",
        input_key=_INPUT_KEY,
        sizes={"128": {"status": "pending", "output_key": None}},
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert from_item(to_item(record)) == record
    assert serialize_value(None) == {"NULL": True}
