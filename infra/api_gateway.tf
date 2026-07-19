# REST API Gateway (AWS_PROXY) for the jobs API.
# LocalStack path-style base: {localstack_endpoint}/_aws/execute-api/{apiId}/{stage}

locals {
  api_stage_name = var.api_stage_name

  # Host-facing base URL for curl / e2e (edge port from .localstack.env).
  api_base_url = (
    "${trimsuffix(var.localstack_endpoint, "/")}/_aws/execute-api/${aws_api_gateway_rest_api.jobs.id}/${local.api_stage_name}"
  )
}

resource "aws_api_gateway_rest_api" "jobs" {
  name        = "${local.name_prefix}-api"
  description = "Thumbnail jobs API (POST /jobs, GET /jobs/{job_id})"

  endpoint_configuration {
    types = ["REGIONAL"]
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-api"
  })
}

resource "aws_api_gateway_resource" "jobs" {
  rest_api_id = aws_api_gateway_rest_api.jobs.id
  parent_id   = aws_api_gateway_rest_api.jobs.root_resource_id
  path_part   = "jobs"
}

resource "aws_api_gateway_method" "create_job" {
  rest_api_id   = aws_api_gateway_rest_api.jobs.id
  resource_id   = aws_api_gateway_resource.jobs.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "create_job" {
  rest_api_id             = aws_api_gateway_rest_api.jobs.id
  resource_id             = aws_api_gateway_resource.jobs.id
  http_method             = aws_api_gateway_method.create_job.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.api_create_job.invoke_arn
}

resource "aws_api_gateway_resource" "job_id" {
  rest_api_id = aws_api_gateway_rest_api.jobs.id
  parent_id   = aws_api_gateway_resource.jobs.id
  path_part   = "{job_id}"
}

resource "aws_api_gateway_method" "get_job" {
  rest_api_id   = aws_api_gateway_rest_api.jobs.id
  resource_id   = aws_api_gateway_resource.job_id.id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "get_job" {
  rest_api_id             = aws_api_gateway_rest_api.jobs.id
  resource_id             = aws_api_gateway_resource.job_id.id
  http_method             = aws_api_gateway_method.get_job.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.api_get_job.invoke_arn
}

resource "aws_lambda_permission" "apigw_create_job" {
  statement_id  = "AllowAPIGatewayInvokeCreateJob"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api_create_job.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.jobs.execution_arn}/*/*"
}

resource "aws_lambda_permission" "apigw_get_job" {
  statement_id  = "AllowAPIGatewayInvokeGetJob"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api_get_job.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.jobs.execution_arn}/*/*"
}

resource "aws_api_gateway_deployment" "jobs" {
  rest_api_id = aws_api_gateway_rest_api.jobs.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.jobs.id,
      aws_api_gateway_method.create_job.id,
      aws_api_gateway_integration.create_job.id,
      aws_api_gateway_resource.job_id.id,
      aws_api_gateway_method.get_job.id,
      aws_api_gateway_integration.get_job.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_api_gateway_integration.create_job,
    aws_api_gateway_integration.get_job,
    aws_lambda_permission.apigw_create_job,
    aws_lambda_permission.apigw_get_job,
  ]
}

resource "aws_api_gateway_stage" "jobs" {
  deployment_id = aws_api_gateway_deployment.jobs.id
  rest_api_id   = aws_api_gateway_rest_api.jobs.id
  stage_name    = local.api_stage_name

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-api-${local.api_stage_name}"
  })
}
