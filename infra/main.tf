# Root module for LocalStack. Resource files:
#   s3.tf — input / output buckets (+ input CORS)
#   dynamodb.tf — jobs table (partition key job_id)
#   sqs.tf — work queue + DLQ + redrive
#   iam_api.tf — IAM roles for create_job / get_job Lambdas
# Follow-on: Lambda, API Gateway.
