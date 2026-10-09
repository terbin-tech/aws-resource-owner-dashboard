data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = local.name
  description        = "Scheduled read-only scan; writes only site/ in the report bucket."
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

module "read_policy" {
  source        = "../policies"
  account_id    = local.account_id
  regions       = local.settings.regions
  cohort_groups = var.cohort_groups
}

resource "aws_iam_role_policy" "lambda_read" {
  name   = "dashboard-read"
  role   = aws_iam_role.lambda.id
  policy = module.read_policy.json
}

# No s3:ListBucket (a missing roster is then 403, read as "no roster") and no
# logs:CreateLogGroup (Terraform owns the log group below, with its 30-day retention).
data "aws_iam_policy_document" "lambda_write" {
  statement {
    sid       = "ReadRoster"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.report.arn}/config/*"]
  }

  statement {
    sid       = "WritePage"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.report.arn}/site/*"]
  }

  statement {
    sid       = "OwnLogStreams"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }
}

resource "aws_iam_role_policy" "lambda_write" {
  name   = "dashboard-write"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_write.json
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 30
}

resource "aws_lambda_function" "dashboard" {
  function_name    = local.name
  description      = "Scheduled page: what did I forget to delete? Read-only."
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.14"
  architectures    = ["arm64"]
  handler          = "dashboard.handler.handler"
  filename         = "${path.module}/${var.lambda_zip}"
  source_code_hash = filebase64sha256("${path.module}/${var.lambda_zip}")
  memory_size      = 512
  timeout          = 600

  environment {
    variables = {
      DASHBOARD_REGIONS       = join(",", local.settings.regions)
      DASHBOARD_MAX_AGE_DAYS  = tostring(local.settings.max_age_days)
      DASHBOARD_LOOKBACK_DAYS = tostring(local.settings.lookback_days)
      DASHBOARD_COHORT_GROUPS = jsonencode(var.cohort_groups)
      DASHBOARD_BUCKET        = aws_s3_bucket.report.bucket
    }
  }

  logging_config {
    log_format            = "JSON"
    application_log_level = "WARN"
    system_log_level      = "WARN"
    log_group             = aws_cloudwatch_log_group.lambda.name
  }

  # The role's policies come first, so no run starts without its permissions. The log group
  # is already a dependency through logging_config.
  depends_on = [aws_iam_role_policy.lambda_read, aws_iam_role_policy.lambda_write]
}

# No Lambda retries: each would be a full rescan against LookupEvents' fixed 2 requests per
# second. The schedule's retry policy covers a run that fails to start.
resource "aws_lambda_function_event_invoke_config" "dashboard" {
  function_name          = aws_lambda_function.dashboard.function_name
  maximum_retry_attempts = 0
}
