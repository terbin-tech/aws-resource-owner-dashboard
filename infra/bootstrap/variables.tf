variable "cohort_groups" {
  type        = map(string)
  description = "Cohort label => IAM group name. Same map as in dashboard.toml and infra/app/terraform.tfvars."
  default     = {}
}

variable "dev_principal" {
  type        = string
  description = "IAM user ARN allowed to assume the dashboard-dev role with MFA. Empty means no dev role is created."
  default     = ""

  validation {
    condition     = var.dev_principal == "" || can(regex("^arn:aws:iam::[0-9]{12}:user/[A-Za-z0-9+=,.@_/-]+$", var.dev_principal))
    error_message = "dev_principal must be empty or an IAM user ARN."
  }
}
