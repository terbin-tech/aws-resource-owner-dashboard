locals {
  create_dev_role = var.dev_principal != ""
  # Regions and age limits from the committed settings.json, which the CLI and infra/app read too.
  settings = jsondecode(file("${path.module}/../../settings.json"))
}

module "read_policy" {
  source        = "../policies"
  account_id    = local.account_id
  regions       = local.settings.regions
  cohort_groups = var.cohort_groups
}

data "aws_iam_policy_document" "dev_trust" {
  count = local.create_dev_role ? 1 : 0

  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [var.dev_principal]
    }

    condition {
      test     = "Bool"
      variable = "aws:MultiFactorAuthPresent"
      values   = ["true"]
    }

    condition {
      test     = "NumericLessThan"
      variable = "aws:MultiFactorAuthAge"
      values   = ["3600"]
    }
  }
}

resource "aws_iam_role" "dashboard_dev" {
  count = local.create_dev_role ? 1 : 0

  name                 = "resource-owner-dashboard-dev"
  description          = "Local read-only runs of the resource owner dashboard (MFA required)."
  assume_role_policy   = data.aws_iam_policy_document.dev_trust[0].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "dashboard_dev_read" {
  count = local.create_dev_role ? 1 : 0

  name   = "dashboard-read"
  role   = aws_iam_role.dashboard_dev[0].id
  policy = module.read_policy.json
}
