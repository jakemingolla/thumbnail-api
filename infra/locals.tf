locals {
  # Shared naming / tags for follow-on resource tickets (S3, SQS, DynamoDB, Lambda).
  name_prefix = var.name_prefix

  input_bucket_name  = coalesce(var.input_bucket_name, "${local.name_prefix}-input")
  output_bucket_name = coalesce(var.output_bucket_name, "${local.name_prefix}-output")

  common_tags = {
    Project     = "thumbnail-api"
    Environment = "local"
    ManagedBy   = "terraform"
  }
}
