output "localstack_endpoint" {
  description = "LocalStack edge URL used by the AWS provider."
  value       = var.localstack_endpoint
}

output "aws_region" {
  description = "Region configured on the AWS provider."
  value       = var.aws_region
}

output "name_prefix" {
  description = "Prefix reserved for resource names in later tickets."
  value       = local.name_prefix
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
