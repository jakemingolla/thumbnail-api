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

output "work_queue_url" {
  description = "URL of the thumbnail work queue (dispatcher send / worker consume)."
  value       = local.work_queue_url
}

output "work_queue_arn" {
  description = "ARN of the thumbnail work queue."
  value       = local.work_queue_arn
}

output "work_queue_name" {
  description = "Name of the thumbnail work queue."
  value       = aws_sqs_queue.work.name
}

output "work_dlq_url" {
  description = "URL of the work-queue dead-letter queue."
  value       = local.work_dlq_url
}

output "work_dlq_arn" {
  description = "ARN of the work-queue dead-letter queue."
  value       = local.work_dlq_arn
}

output "work_dlq_name" {
  description = "Name of the work-queue dead-letter queue."
  value       = aws_sqs_queue.work_dlq.name
}

output "sqs_max_receive_count" {
  description = "Redrive maxReceiveCount for the work queue (see job-state-machine.md)."
  value       = var.sqs_max_receive_count
}

output "api_create_job_role_arn" {
  description = "IAM role ARN for the create_job Lambda (DynamoDB PutItem + input-bucket presign PutObject)."
  value       = aws_iam_role.api_create_job.arn
}

output "api_create_job_role_name" {
  description = "IAM role name for the create_job Lambda."
  value       = aws_iam_role.api_create_job.name
}

output "api_get_job_role_arn" {
  description = "IAM role ARN for the get_job Lambda (DynamoDB GetItem only)."
  value       = aws_iam_role.api_get_job.arn
}

output "api_get_job_role_name" {
  description = "IAM role name for the get_job Lambda."
  value       = aws_iam_role.api_get_job.name
}

output "api_create_job_function_name" {
  description = "Lambda function name for create_job (direct invoke / API Gateway)."
  value       = aws_lambda_function.api_create_job.function_name
}

output "api_create_job_function_arn" {
  description = "ARN of the create_job Lambda."
  value       = aws_lambda_function.api_create_job.arn
}

output "api_get_job_function_name" {
  description = "Lambda function name for get_job (direct invoke / API Gateway)."
  value       = aws_lambda_function.api_get_job.function_name
}

output "api_get_job_function_arn" {
  description = "ARN of the get_job Lambda."
  value       = aws_lambda_function.api_get_job.arn
}

output "dispatcher_role_arn" {
  description = "IAM role ARN for the dispatcher Lambda (SQS SendMessage + jobs UpdateItem/GetItem)."
  value       = aws_iam_role.dispatcher.arn
}

output "dispatcher_role_name" {
  description = "IAM role name for the dispatcher Lambda."
  value       = aws_iam_role.dispatcher.name
}

output "worker_role_arn" {
  description = "IAM role ARN for the worker Lambda (SQS consume + input GetObject + output PutObject + jobs updates)."
  value       = aws_iam_role.worker.arn
}

output "worker_role_name" {
  description = "IAM role name for the worker Lambda."
  value       = aws_iam_role.worker.name
}
