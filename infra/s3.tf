# Input and output buckets for LocalStack.
# Object key layout is a contract (docs/specification/s3-keys.md), not Terraform resources.

resource "aws_s3_bucket" "input" {
  bucket        = local.input_bucket_name
  force_destroy = true

  tags = merge(local.common_tags, {
    Role = "input"
  })
}

resource "aws_s3_bucket" "output" {
  bucket        = local.output_bucket_name
  force_destroy = true

  tags = merge(local.common_tags, {
    Role = "output"
  })
}

# Browser uploads to presigned PUT URLs need CORS on the input bucket.
# Non-browser clients (CLI, SDK) ignore CORS and work without this rule.
resource "aws_s3_bucket_cors_configuration" "input" {
  bucket = aws_s3_bucket.input.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "HEAD", "PUT"]
    allowed_origins = ["*"]
    expose_headers  = ["ETag", "Content-Type"]
    max_age_seconds = 3000
  }
}
