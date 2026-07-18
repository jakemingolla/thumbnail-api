provider "aws" {
  access_key = var.aws_access_key
  secret_key = var.aws_secret_key
  region     = var.aws_region

  # Required for LocalStack: path-style S3 so presigned URLs resolve correctly.
  s3_use_path_style = true

  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {
    apigateway = var.localstack_endpoint
    dynamodb   = var.localstack_endpoint
    iam        = var.localstack_endpoint
    lambda     = var.localstack_endpoint
    s3         = var.localstack_endpoint
    sqs        = var.localstack_endpoint
    sts        = var.localstack_endpoint
  }
}
