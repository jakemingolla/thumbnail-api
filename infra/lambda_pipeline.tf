# Pipeline Lambdas. Artifact: dist/lambda/pipeline.zip (just package).
# Dispatcher: S3 ObjectCreated on uploads/ → SQS fan-out.
# Worker: SQS → resize → output + job status (batch size 1).

locals {
  pipeline_lambda_zip = "${path.module}/../dist/lambda/pipeline.zip"

  # Shared env shape with API Lambdas (Config requires all keys).
  # AWS_ENDPOINT_URL must be the in-Lambda LocalStack URL — not the host edge
  # port from .localstack.env (see lambda_aws_endpoint_url).
  pipeline_lambda_environment = {
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

resource "aws_lambda_function" "dispatcher" {
  function_name = "${local.name_prefix}-dispatcher"
  description   = "S3 ObjectCreated → SQS size fan-out + job pending→processing"
  role          = aws_iam_role.dispatcher.arn
  handler       = "thumbnail_api.handlers.dispatcher.handler"
  runtime       = var.lambda_runtime
  architectures = var.lambda_architectures

  filename         = local.pipeline_lambda_zip
  source_code_hash = filebase64sha256(local.pipeline_lambda_zip)

  timeout     = var.dispatcher_lambda_timeout_seconds
  memory_size = var.dispatcher_lambda_memory_size

  environment {
    variables = local.pipeline_lambda_environment
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-dispatcher"
    Role = "dispatcher"
  })

  depends_on = [
    aws_iam_role_policy.dispatcher,
  ]
}

resource "aws_lambda_permission" "dispatcher_s3" {
  statement_id  = "AllowS3InvokeDispatcher"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dispatcher.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.input.arn
}

# ObjectCreated under uploads/ (docs/specification/s3-keys.md). Unexpected keys
# are ignored by the handler; the prefix keeps non-upload traffic off the Lambda.
resource "aws_s3_bucket_notification" "input_dispatcher" {
  bucket = aws_s3_bucket.input.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.dispatcher.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "uploads/"
  }

  depends_on = [aws_lambda_permission.dispatcher_s3]
}

resource "aws_lambda_function" "worker" {
  function_name = "${local.name_prefix}-worker"
  description   = "SQS work queue — resize one thumbnail size and update job status"
  role          = aws_iam_role.worker.arn
  handler       = "thumbnail_api.handlers.worker.handler"
  runtime       = var.lambda_runtime
  architectures = var.lambda_architectures

  filename         = local.pipeline_lambda_zip
  source_code_hash = filebase64sha256(local.pipeline_lambda_zip)

  timeout     = var.worker_lambda_timeout_seconds
  memory_size = var.worker_lambda_memory_size

  environment {
    variables = local.pipeline_lambda_environment
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-worker"
    Role = "worker"
  })

  depends_on = [
    aws_iam_role_policy.worker,
  ]
}

# Batch size 1 per docs/specification/sqs-messages.md (worker batch size).
resource "aws_lambda_event_source_mapping" "worker_sqs" {
  event_source_arn = aws_sqs_queue.work.arn
  function_name    = aws_lambda_function.worker.arn
  batch_size       = 1
  enabled          = true

  depends_on = [
    aws_iam_role_policy.worker,
  ]
}
