variable "alarm_email" {
  type        = string
  description = "Optional. Empty (the default): no email subscription, alarms show only in the CloudWatch console. Set it to the teacher list to get alarm emails (the subscription must then be confirmed within 2 days)."
  default     = ""
  sensitive   = true

  validation {
    condition     = var.alarm_email == "" || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alarm_email))
    error_message = "alarm_email must be empty or one email address."
  }
}

variable "cohort_groups" {
  type        = map(string)
  description = "Cohort label => IAM group name. Same map as in dashboard.toml and infra/bootstrap/terraform.tfvars."
  default     = {}
}

variable "lambda_zip" {
  type        = string
  description = "Bundle built by scripts/build_lambda.py, relative to infra/app."
  default     = "../../build/lambda.zip"
}

variable "schedule_enabled" {
  type        = bool
  description = "Kill switch: false disables the scheduled runs and silences the no-run alarm."
  default     = true
}
