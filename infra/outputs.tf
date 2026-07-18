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
