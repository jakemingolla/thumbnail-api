# IAM for pipeline Lambdas (dispatcher / worker). Distinct from API roles (THUMB-015).
# Dispatcher: SQS send + jobs status update. Worker: SQS consume + input read + output write + jobs updates.
# No API-only permissions (presigned PutObject for create_job, GetItem-only for get_job).

data "aws_iam_policy_document" "pipeline_lambda_assume_role" {
  statement {
    sid    = "LambdaAssumeRole"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

data "aws_iam_policy_document" "pipeline_lambda_logs" {
  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:*:*:*"]
  }
}

data "aws_iam_policy_document" "dispatcher" {
  source_policy_documents = [data.aws_iam_policy_document.pipeline_lambda_logs.json]

  # S3 ObjectCreated payload is delivered on the invoke; dispatcher does not GetObject.
  statement {
    sid    = "WorkQueueSend"
    effect = "Allow"
    actions = [
      "sqs:SendMessage",
    ]
    resources = [aws_sqs_queue.work.arn]
  }

  # mark_job_processing uses UpdateItem; GetItem on conditional-check fallback / idempotency.
  statement {
    sid    = "JobsUpdateStatus"
    effect = "Allow"
    actions = [
      "dynamodb:UpdateItem",
      "dynamodb:GetItem",
    ]
    resources = [aws_dynamodb_table.jobs.arn]
  }
}

data "aws_iam_policy_document" "worker" {
  source_policy_documents = [data.aws_iam_policy_document.pipeline_lambda_logs.json]

  # Required for SQS event source mapping (poll / delete after success).
  statement {
    sid    = "WorkQueueConsume"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.work.arn]
  }

  statement {
    sid    = "InputReadOriginal"
    effect = "Allow"
    actions = [
      "s3:GetObject",
    ]
    resources = ["${aws_s3_bucket.input.arn}/uploads/*"]
  }

  statement {
    sid    = "OutputWriteThumbnail"
    effect = "Allow"
    actions = [
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.output.arn}/thumbnails/*"]
  }

  # claim/complete/fail size + rollup use UpdateItem; GetItem on idempotent fallbacks.
  statement {
    sid    = "JobsUpdateSizeAndRollup"
    effect = "Allow"
    actions = [
      "dynamodb:UpdateItem",
      "dynamodb:GetItem",
    ]
    resources = [aws_dynamodb_table.jobs.arn]
  }
}

resource "aws_iam_role" "dispatcher" {
  name               = "${local.name_prefix}-dispatcher"
  assume_role_policy = data.aws_iam_policy_document.pipeline_lambda_assume_role.json

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-dispatcher"
    Role = "dispatcher"
  })
}

resource "aws_iam_role" "worker" {
  name               = "${local.name_prefix}-worker"
  assume_role_policy = data.aws_iam_policy_document.pipeline_lambda_assume_role.json

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-worker"
    Role = "worker"
  })
}

resource "aws_iam_role_policy" "dispatcher" {
  name   = "${local.name_prefix}-dispatcher"
  role   = aws_iam_role.dispatcher.id
  policy = data.aws_iam_policy_document.dispatcher.json
}

resource "aws_iam_role_policy" "worker" {
  name   = "${local.name_prefix}-worker"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}
