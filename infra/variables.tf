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

variable "input_bucket_name" {
  description = "Override for the input (upload) bucket name. Defaults to \"<name_prefix>-input\"."
  type        = string
  default     = null
}

variable "output_bucket_name" {
  description = "Override for the output (thumbnail) bucket name. Defaults to \"<name_prefix>-output\"."
  type        = string
  default     = null
}

variable "jobs_table_name" {
  description = "Override for the DynamoDB jobs table name. Defaults to \"<name_prefix>-jobs\"."
  type        = string
  default     = null
}

variable "sqs_max_receive_count" {
  description = <<-EOT
    Receives before SQS redrives a message from the work queue to the DLQ.
    Must stay aligned with docs/specification/job-state-machine.md (Retries and DLQ).
    Default 5: enough headroom for transient S3/DynamoDB blips; poison messages
    still land on the DLQ without unbounded retry.
  EOT
  type        = number
  default     = 5

  validation {
    condition     = var.sqs_max_receive_count >= 1 && var.sqs_max_receive_count <= 1000
    error_message = "sqs_max_receive_count must be between 1 and 1000."
  }
}

variable "sqs_visibility_timeout_seconds" {
  description = "Work-queue visibility timeout. Raise with worker Lambda timeout (THUMB-022)."
  type        = number
  default     = 60
}

variable "sqs_message_retention_seconds" {
  description = "How long messages stay on the work queue if not deleted."
  type        = number
  default     = 345600 # 4 days
}

variable "sqs_dlq_message_retention_seconds" {
  description = "How long poison messages stay on the DLQ for inspection."
  type        = number
  default     = 1209600 # 14 days
}

variable "sqs_receive_wait_time_seconds" {
  description = "Long-polling wait on the work queue (0 = short poll)."
  type        = number
  default     = 20
}

variable "lambda_runtime" {
  description = "Python runtime for API Lambdas. Must match just package / LocalStack."
  type        = string
  default     = "python3.13"
}

variable "lambda_architectures" {
  description = <<-EOT
    Lambda instruction set. Must match wheels from just package
    (Apple Silicon → arm64 / aarch64-unknown-linux-gnu; otherwise x86_64).
  EOT
  type        = list(string)
  default     = ["arm64"]
}

variable "lambda_environment" {
  description = "ENVIRONMENT value injected into Lambda (Config.environment)."
  type        = string
  default     = "local"
}

variable "lambda_aws_endpoint_url" {
  description = <<-EOT
    AWS_ENDPOINT_URL inside Lambda containers. Use the in-network LocalStack
    edge (default http://localhost.localstack.cloud:4566), not the host-mapped
    LOCALSTACK_ENDPOINT from .localstack.env — host 127.0.0.1:<edge-port> is
    unreachable from Lambda Docker sidecars.
  EOT
  type        = string
  default     = "http://localhost.localstack.cloud:4566"
}

variable "api_lambda_timeout_seconds" {
  description = "Timeout for create_job / get_job Lambdas."
  type        = number
  default     = 30
}

variable "api_lambda_memory_size" {
  description = "Memory (MB) for create_job / get_job Lambdas."
  type        = number
  default     = 256
}

variable "dispatcher_lambda_timeout_seconds" {
  description = "Timeout for the S3 → SQS dispatcher Lambda."
  type        = number
  default     = 30
}

variable "dispatcher_lambda_memory_size" {
  description = "Memory (MB) for the S3 → SQS dispatcher Lambda."
  type        = number
  default     = 256
}

variable "thumbnail_sizes" {
  description = "Configured thumbnail sizes (pixels) for THUMBNAIL_SIZES env. Must match docs/specification/sqs-messages.md."
  type        = list(number)
  default     = [128, 256, 512]

  validation {
    condition     = length(var.thumbnail_sizes) > 0 && alltrue([for size in var.thumbnail_sizes : size > 0])
    error_message = "thumbnail_sizes must be a non-empty list of positive integers."
  }
}
