# Pipeline Lambda: worker (SQS → resize → output + job status).
# Artifact: dist/lambda/pipeline.zip (just package). Dispatcher Lambda: THUMB-019.

locals {
  pipeline_lambda_zip = "${path.module}/../dist/lambda/pipeline.zip"
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
    # Shared Config keys (same map as API Lambdas).
    variables = local.api_lambda_environment
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
