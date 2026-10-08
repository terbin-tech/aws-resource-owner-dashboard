data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  # Fixed, not a variable: the Lambda (GLOBAL_REGION in src/dashboard/config.py), the smoke test
  # and both backend.hcl files assume it.
  region = "us-east-1"
}
