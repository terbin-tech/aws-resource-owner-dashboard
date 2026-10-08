variable "account_id" {
  type        = string
  description = "Account that owns the IAM users and cohort groups."
}

variable "cohort_groups" {
  type        = map(string)
  description = "Cohort label => IAM group name. iam:GetGroup is allowed on these groups only."
  default     = {}
}

variable "regions" {
  type        = list(string)
  description = "Regions the dashboard may read: `regions` from settings.json."

  validation {
    condition     = length(var.regions) > 0
    error_message = "regions must not be empty."
  }
}
