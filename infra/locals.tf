locals {
  # Shared naming / tags for follow-on resource tickets (S3, SQS, DynamoDB, Lambda).
  name_prefix = var.name_prefix

  jobs_table_name = coalesce(var.jobs_table_name, "${local.name_prefix}-jobs")

  common_tags = {
    Project     = "thumbnail-api"
    Environment = "local"
    ManagedBy   = "terraform"
  }
}
