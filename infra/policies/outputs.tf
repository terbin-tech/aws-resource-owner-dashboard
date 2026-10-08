output "json" {
  type        = string
  description = "The read policy document (no S3, no writes)."
  value       = data.aws_iam_policy_document.read.json
}
