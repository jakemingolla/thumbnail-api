# IAM for API Lambdas (create_job / get_job). Distinct from pipeline roles (THUMB-021).
# No SQS send/consume or output-bucket write — API handlers only touch jobs + input presign.

data "aws_iam_policy_document" "lambda_assume_role" {
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

data "aws_iam_policy_document" "api_lambda_logs" {
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

data "aws_iam_policy_document" "api_create_job" {
  source_policy_documents = [data.aws_iam_policy_document.api_lambda_logs.json]

  statement {
    sid    = "JobsPutItem"
    effect = "Allow"
    actions = [
      "dynamodb:PutItem",
    ]
    resources = [aws_dynamodb_table.jobs.arn]
  }

  # Presigned PUT URLs are signed with the Lambda credentials; PutObject must be allowed
  # on the input object key even though the client (not the Lambda) performs the upload.
  statement {
    sid    = "InputPresignedPut"
    effect = "Allow"
    actions = [
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.input.arn}/uploads/*"]
  }
}

data "aws_iam_policy_document" "api_get_job" {
  source_policy_documents = [data.aws_iam_policy_document.api_lambda_logs.json]

  statement {
    sid    = "JobsGetItem"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
    ]
    resources = [aws_dynamodb_table.jobs.arn]
  }
}

resource "aws_iam_role" "api_create_job" {
  name               = "${local.name_prefix}-api-create-job"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-api-create-job"
    Role = "api-create-job"
  })
}

resource "aws_iam_role" "api_get_job" {
  name               = "${local.name_prefix}-api-get-job"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-api-get-job"
    Role = "api-get-job"
  })
}

resource "aws_iam_role_policy" "api_create_job" {
  name   = "${local.name_prefix}-api-create-job"
  role   = aws_iam_role.api_create_job.id
  policy = data.aws_iam_policy_document.api_create_job.json
}

resource "aws_iam_role_policy" "api_get_job" {
  name   = "${local.name_prefix}-api-get-job"
  role   = aws_iam_role.api_get_job.id
  policy = data.aws_iam_policy_document.api_get_job.json
}
