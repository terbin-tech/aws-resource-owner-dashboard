# The one read policy for the Lambda role (infra/app) and the dashboard-dev role
# (infra/bootstrap), so the two can't drift.

locals {
  # CloudTrail records IAM events such as CreateUser in us-east-1, so lookups need that region
  # even when regions leaves it out.
  lookup_regions = distinct(concat(var.regions, ["us-east-1"]))
}

data "aws_iam_policy_document" "read" {
  statement {
    sid = "RegionalInventory"
    actions = [
      "ec2:DescribeInstances",
      "ec2:DescribeVolumes",
      "ec2:DescribeAddresses",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DescribeNatGateways",
      "elasticloadbalancing:DescribeLoadBalancers",
      "elasticloadbalancing:DescribeTags",
      "eks:ListClusters",
      "eks:DescribeCluster",
      "eks:ListNodegroups",
      "eks:DescribeNodegroup",
      "eks:DescribeClusterVersions",
      "autoscaling:DescribeAutoScalingGroups",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = var.regions
    }
  }

  statement {
    sid       = "CloudTrailCreators"
    actions   = ["cloudtrail:LookupEvents"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = local.lookup_regions
    }
  }

  statement {
    sid       = "IamUsers"
    actions   = ["iam:ListUsers"]
    resources = ["arn:aws:iam::${var.account_id}:user/*"]
  }

  dynamic "statement" {
    for_each = length(var.cohort_groups) > 0 ? [1] : []
    content {
      sid     = "CohortGroups"
      actions = ["iam:GetGroup"]
      # Second form covers groups created with a path (group/<path>/<name>).
      resources = flatten([
        for g in values(var.cohort_groups) : [
          "arn:aws:iam::${var.account_id}:group/${g}",
          "arn:aws:iam::${var.account_id}:group/*/${g}",
        ]
      ])
    }
  }
}
