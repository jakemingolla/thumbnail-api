"""Shared fixtures for the LocalStack e2e harness.

Run via ``just test-e2e`` (Compose → package → terraform apply → pytest).
Add scenarios in the same PR as the feature; do not create a parallel harness.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import TypedDict, cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INFRA_DIR = REPO_ROOT / "infra"


class AwsCredentials(TypedDict):
    aws_access_key_id: str
    aws_secret_access_key: str
    region_name: str


def _endpoint_from_env() -> str:
    endpoint = os.environ.get("LOCALSTACK_ENDPOINT") or os.environ.get("AWS_ENDPOINT_URL")
    if not endpoint:
        pytest.fail(
            "e2e fixture failed: LOCALSTACK_ENDPOINT / AWS_ENDPOINT_URL unset. "
            "Run via `just test-e2e` (harness loads .localstack.env)."
        )
    return endpoint.rstrip("/")


@pytest.fixture(scope="session")
def aws_credentials() -> AwsCredentials:
    """Return dummy LocalStack credentials (test/test) plus region."""
    return {
        "aws_access_key_id": os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        "aws_secret_access_key": os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
        "region_name": (
            os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
        ),
    }


@pytest.fixture(scope="session")
def localstack_endpoint(aws_credentials: AwsCredentials) -> str:
    """Return LocalStack edge URL (from harness env)."""
    endpoint = _endpoint_from_env()
    # Print once so CI logs show harness context on failure.
    print(
        f"\ne2e harness: endpoint={endpoint} "
        f"region={aws_credentials['region_name']} "
        f"access_key={aws_credentials['aws_access_key_id']}\n"
    )
    return endpoint


@pytest.fixture(scope="session")
def terraform_outputs(localstack_endpoint: str) -> dict[str, object]:
    """Return full ``terraform output -json`` map keyed by output name.

    Each value is the Terraform JSON object ``{value, type, sensitive}``.
    """
    terraform = shutil.which("terraform")
    if terraform is None:
        pytest.fail(
            "e2e fixture `terraform_outputs` failed: terraform not on PATH "
            f"(LocalStack endpoint={localstack_endpoint})"
        )
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv; terraform from PATH
            [terraform, "output", "-json"],
            cwd=INFRA_DIR,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        pytest.fail(
            "e2e fixture `terraform_outputs` failed: "
            f"`terraform output -json` exited {exc.returncode} "
            f"(LocalStack endpoint={localstack_endpoint}).\n"
            f"stdout:\n{exc.stdout}\nstderr:\n{exc.stderr}"
        )

    try:
        parsed: object = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            "e2e fixture `terraform_outputs` failed: invalid JSON "
            f"(LocalStack endpoint={localstack_endpoint}): {exc}\n{proc.stdout}"
        )

    if not isinstance(parsed, dict):
        pytest.fail(
            "e2e fixture `terraform_outputs` failed: expected a JSON object "
            f"(LocalStack endpoint={localstack_endpoint})"
        )

    return cast("dict[str, object]", parsed)


@pytest.fixture(scope="session")
def tf_outputs(
    terraform_outputs: dict[str, object],
    localstack_endpoint: str,
) -> dict[str, object]:
    """Return flattened Terraform outputs: name → value."""
    flat: dict[str, object] = {}
    for name, entry in terraform_outputs.items():
        if not isinstance(entry, dict) or "value" not in entry:
            pytest.fail(
                f"e2e fixture `tf_outputs` failed: output {name!r} missing value "
                f"(LocalStack endpoint={localstack_endpoint}). "
                f"Raw entry: {entry!r}"
            )
        flat[name] = entry["value"]
    return flat
