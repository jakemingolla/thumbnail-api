# Root module for LocalStack. Resource files:
#   s3.tf — input / output buckets (+ input CORS)
#   dynamodb.tf — jobs table (partition key job_id)
#   sqs.tf — work queue + DLQ + redrive
#   iam_api.tf — IAM roles for create_job / get_job Lambdas
#   iam_pipeline.tf — IAM roles for dispatcher / worker Lambdas
#   lambda_api.tf — create_job / get_job functions (dist/lambda/api.zip)
#   lambda_pipeline.tf — dispatcher (+ S3 notification); worker: THUMB-022
#   api_gateway.tf — REST API + AWS_PROXY routes for POST/GET /jobs
# Follow-on: worker Lambda (THUMB-022).

