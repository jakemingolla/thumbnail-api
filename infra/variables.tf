variable "aws_region" {
  description = "AWS region used by the provider (LocalStack accepts any; keep stable)."
  type        = string
  default     = "us-east-1"
}

variable "aws_access_key" {
  description = "Mock access key for LocalStack (not real AWS)."
  type        = string
  default     = "test"
  sensitive   = true
}

variable "aws_secret_key" {
  description = "Mock secret key for LocalStack (not real AWS)."
  type        = string
  default     = "test"
  sensitive   = true
}

variable "localstack_endpoint" {
  description = "LocalStack edge URL. Must match docker-compose host bind."
  type        = string
  default     = "http://localhost:4566"
}

variable "name_prefix" {
  description = "Prefix for resource names (buckets, queues, tables, etc.)."
  type        = string
  default     = "thumbnail"
}

variable "jobs_table_name" {
  description = "Override for the DynamoDB jobs table name. Defaults to \"<name_prefix>-jobs\"."
  type        = string
  default     = null
}
