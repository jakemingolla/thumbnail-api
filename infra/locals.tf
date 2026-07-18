locals {
  # Shared naming / tags for follow-on resource tickets (S3, SQS, DynamoDB, Lambda).
  name_prefix = var.name_prefix

  common_tags = {
    Project     = "thumbnail-api"
    Environment = "local"
    ManagedBy   = "terraform"
  }

  # Convenience aliases for Lambda env wiring (THUMB-019 / THUMB-022).
  work_queue_url = aws_sqs_queue.work.url
  work_queue_arn = aws_sqs_queue.work.arn
  work_dlq_url   = aws_sqs_queue.work_dlq.url
  work_dlq_arn   = aws_sqs_queue.work_dlq.arn
}
