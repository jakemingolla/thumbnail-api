# API Lambdas (create_job / get_job). Artifact: dist/lambda/api.zip (just package).
# HTTP routes: THUMB-017. Pipeline functions: THUMB-019 / THUMB-022.

locals {
  api_lambda_zip = "${path.module}/../dist/lambda/api.zip"

  # Shared env for both API handlers. Config requires all keys even when unused
  # (get_job does not touch S3/SQS; create_job does not send to the work queue).
  # AWS_ENDPOINT_URL must be the in-Lambda LocalStack URL — not the host edge
  # port from .localstack.env (see lambda_aws_endpoint_url).
  api_lambda_environment = {
    ENVIRONMENT      = var.lambda_environment
    INPUT_BUCKET     = aws_s3_bucket.input.bucket
    OUTPUT_BUCKET    = aws_s3_bucket.output.bucket
    JOBS_TABLE       = aws_dynamodb_table.jobs.name
    QUEUE_URL        = aws_sqs_queue.work.url
    AWS_ENDPOINT_URL = var.lambda_aws_endpoint_url
    AWS_REGION       = var.aws_region
    THUMBNAIL_SIZES  = join(",", [for size in var.thumbnail_sizes : tostring(size)])
  }
}

resource "aws_lambda_function" "api_create_job" {
  function_name = "${local.name_prefix}-api-create-job"
  description   = "POST /jobs — create pending job + presigned upload URL"
  role          = aws_iam_role.api_create_job.arn
  handler       = "thumbnail_api.handlers.create_job.handler"
  runtime       = var.lambda_runtime
  architectures = var.lambda_architectures

  filename         = local.api_lambda_zip
  source_code_hash = filebase64sha256(local.api_lambda_zip)

  timeout     = var.api_lambda_timeout_seconds
  memory_size = var.api_lambda_memory_size

  environment {
    variables = local.api_lambda_environment
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-api-create-job"
    Role = "api-create-job"
  })

  depends_on = [
    aws_iam_role_policy.api_create_job,
  ]
}

resource "aws_lambda_function" "api_get_job" {
  function_name = "${local.name_prefix}-api-get-job"
  description   = "GET /jobs/{job_id} — poll job status"
  role          = aws_iam_role.api_get_job.arn
  handler       = "thumbnail_api.handlers.get_job.handler"
  runtime       = var.lambda_runtime
  architectures = var.lambda_architectures

  filename         = local.api_lambda_zip
  source_code_hash = filebase64sha256(local.api_lambda_zip)

  timeout     = var.api_lambda_timeout_seconds
  memory_size = var.api_lambda_memory_size

  environment {
    variables = local.api_lambda_environment
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-api-get-job"
    Role = "api-get-job"
  })

  depends_on = [
    aws_iam_role_policy.api_get_job,
  ]
}
