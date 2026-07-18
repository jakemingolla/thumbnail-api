# Jobs table: consumer-facing source of truth for create/get and pipeline updates.
# Item shape and key design: docs/specification/job-state-machine.md (partition key job_id, no sort key).
# GSIs, TTL, and streams are out of scope for v1.

resource "aws_dynamodb_table" "jobs" {
  name         = local.jobs_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }

  tags = merge(local.common_tags, {
    Name = local.jobs_table_name
    Role = "jobs"
  })
}
