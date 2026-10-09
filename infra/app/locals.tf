data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  name       = "resource-owner-dashboard"
  # Fixed, not a variable: the Lambda (GLOBAL_REGION in src/dashboard/config.py), the smoke test
  # and both backend.hcl files assume it.
  region = "us-east-1"
  # Regions and age limits from the committed settings.json, which the CLI and infra/bootstrap read too.
  settings = jsondecode(file("${path.module}/../../settings.json"))
}
