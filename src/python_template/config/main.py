from pathlib import Path

from .types import Config


def get_config(*, env_file: Path | str | None = ".env") -> Config:
    """Load settings from the environment and an optional dotenv file."""
    # pydantic-settings fills required fields from the environment at runtime;
    # the type checker only sees the constructor signature, so call-arg is expected.
    return Config(_env_file=env_file)  # type: ignore[call-arg]
