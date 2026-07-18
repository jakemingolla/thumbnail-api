# Work queue + DLQ for thumbnail size fan-out (dispatcher → worker).
# Event source mapping to the worker Lambda is THUMB-022.

resource "aws_sqs_queue" "work_dlq" {
  name = "${local.name_prefix}-work-dlq"

  # Keep poison messages long enough for local debugging; not a production retention policy.
  message_retention_seconds = var.sqs_dlq_message_retention_seconds

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-work-dlq"
    Role = "dlq"
  })
}

resource "aws_sqs_queue" "work" {
  name = "${local.name_prefix}-work"

  # Must cover a single worker invocation; raise alongside Lambda timeout in THUMB-022.
  visibility_timeout_seconds = var.sqs_visibility_timeout_seconds
  message_retention_seconds  = var.sqs_message_retention_seconds
  receive_wait_time_seconds  = var.sqs_receive_wait_time_seconds

  # After maxReceiveCount receives without delete, SQS redrives to the DLQ.
  # Chosen value: see var.sqs_max_receive_count (documented in job-state-machine.md).
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.work_dlq.arn
    maxReceiveCount     = var.sqs_max_receive_count
  })

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-work"
    Role = "work"
  })
}

# Allow the main queue to send failed messages to the DLQ (required for redrive).
resource "aws_sqs_queue_redrive_allow_policy" "work_dlq" {
  queue_url = aws_sqs_queue.work_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.work.arn]
  })
}
