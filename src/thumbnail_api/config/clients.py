import boto3
from botocore.client import BaseClient
from botocore.config import Config as BotoConfig

from .types import Config

_PATH_STYLE_S3 = BotoConfig(s3={"addressing_style": "path"})


def get_s3_client(config: Config) -> BaseClient:
    """Return an S3 client pointed at LocalStack with path-style addressing."""
    return boto3.client(
        "s3",
        endpoint_url=config.aws_endpoint_url,
        region_name=config.aws_region,
        config=_PATH_STYLE_S3,
    )


def get_dynamodb_client(config: Config) -> BaseClient:
    """Return a DynamoDB client pointed at LocalStack."""
    return boto3.client(
        "dynamodb",
        endpoint_url=config.aws_endpoint_url,
        region_name=config.aws_region,
    )


def get_sqs_client(config: Config) -> BaseClient:
    """Return an SQS client pointed at LocalStack."""
    return boto3.client(
        "sqs",
        endpoint_url=config.aws_endpoint_url,
        region_name=config.aws_region,
    )
