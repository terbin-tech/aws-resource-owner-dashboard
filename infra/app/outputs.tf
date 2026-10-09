output "errors_alarm_name" {
  type        = string
  description = "Name of the alarm that fires when a run fails."
  value       = aws_cloudwatch_metric_alarm.errors.alarm_name
}

output "function_name" {
  type        = string
  description = "Name of the Lambda function, for the smoke test."
  value       = aws_lambda_function.dashboard.function_name
}

output "report_bucket" {
  type        = string
  description = "Contains the account ID: read it with -raw into a variable, never print it."
  value       = aws_s3_bucket.report.bucket
  sensitive   = true
}
