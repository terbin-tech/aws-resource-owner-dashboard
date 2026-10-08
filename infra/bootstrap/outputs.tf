output "dev_role_arn" {
  type        = string
  description = "ARN of the dashboard-dev role, or null when dev_principal is empty."
  value       = try(aws_iam_role.dashboard_dev[0].arn, null)
}

output "state_bucket" {
  type        = string
  description = "Name of the Terraform state bucket. It contains the account ID."
  value       = aws_s3_bucket.state.bucket
}
