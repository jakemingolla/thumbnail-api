output "localstack_endpoint" {
  description = "LocalStack edge URL used by the AWS provider."
  value       = var.localstack_endpoint
}

output "aws_region" {
  description = "Region configured on the AWS provider."
  value       = var.aws_region
}

output "name_prefix" {
  description = "Prefix used for default resource names."
  value       = local.name_prefix
}

output "input_bucket_name" {
  description = "S3 bucket for client uploads of original images (INPUT_BUCKET)."
  value       = aws_s3_bucket.input.bucket
}

output "output_bucket_name" {
  description = "S3 bucket for worker-written thumbnails (OUTPUT_BUCKET)."
  value       = aws_s3_bucket.output.bucket
}

output "jobs_table_name" {
  description = "DynamoDB jobs table name (JOBS_TABLE for Lambda env injection)."
  value       = aws_dynamodb_table.jobs.name
}

output "jobs_table_arn" {
  description = "ARN of the DynamoDB jobs table."
  value       = aws_dynamodb_table.jobs.arn
}
