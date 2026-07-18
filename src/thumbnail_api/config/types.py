from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Default must match docs/specification/sqs-messages.md "Configured sizes (v1)".
DEFAULT_THUMBNAIL_SIZES: list[int] = [128, 256, 512]


class Config(BaseSettings):
    """Runtime settings loaded from the environment (and optional `.env`).

    Missing required values fail fast at load time.
    """

    model_config = SettingsConfigDict(env_file=".env")

    environment: str = Field(description="The environment to run the project in.")

    input_bucket: str = Field(
        min_length=1,
        description="S3 bucket for original uploads (INPUT_BUCKET).",
    )
    output_bucket: str = Field(
        min_length=1,
        description="S3 bucket for thumbnail objects (OUTPUT_BUCKET).",
    )
    jobs_table: str = Field(
        min_length=1,
        description="DynamoDB table name for job records (JOBS_TABLE).",
    )
    queue_url: str = Field(
        min_length=1,
        description="SQS work queue URL (QUEUE_URL).",
    )
    aws_endpoint_url: str = Field(
        min_length=1,
        description="LocalStack edge URL for boto3 clients (AWS_ENDPOINT_URL).",
    )
    aws_region: str = Field(
        default="us-east-1",
        min_length=1,
        description="AWS region for boto3 clients (AWS_REGION).",
    )
    thumbnail_sizes: Annotated[list[int], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_THUMBNAIL_SIZES),
        description=(
            "Configured thumbnail sizes in pixels (THUMBNAIL_SIZES). "
            "Comma-separated integers; defaults to 128,256,512."
        ),
    )

    @field_validator("thumbnail_sizes", mode="before")
    @classmethod
    def parse_thumbnail_sizes(cls, value: object) -> object:
        """Accept comma-separated size strings from the environment."""
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",") if part.strip()]
            if not parts:
                msg = "thumbnail_sizes must not be empty"
                raise ValueError(msg)
            return parts
        return value

    @field_validator("thumbnail_sizes")
    @classmethod
    def validate_thumbnail_sizes(cls, value: list[int]) -> list[int]:
        """Reject empty lists and non-positive sizes."""
        if not value:
            msg = "thumbnail_sizes must not be empty"
            raise ValueError(msg)
        if any(size <= 0 for size in value):
            msg = "thumbnail_sizes must be positive integers"
            raise ValueError(msg)
        return value
