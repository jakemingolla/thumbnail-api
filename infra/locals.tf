locals {
  # Shared naming / tags for follow-on resource tickets (S3, SQS, DynamoDB, Lambda).
  name_prefix = var.name_prefix

  common_tags = {
    Project     = "thumbnail-api"
    Environment = "local"
    ManagedBy   = "terraform"
  }
}
