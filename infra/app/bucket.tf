locals {
  # Account regional namespace, like the state bucket: only this account can create the name.
  report_bucket = "${local.name}-report-${local.account_id}-${local.region}-an"
}

resource "aws_s3_bucket" "report" {
  bucket           = local.report_bucket
  bucket_namespace = "account-regional"
  force_destroy    = true # holds only data that can be rebuilt (the page, the roster), so destroy can empty it
}

resource "aws_s3_bucket_public_access_block" "report" {
  bucket                  = aws_s3_bucket.report.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "report" {
  bucket = aws_s3_bucket.report.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "report" {
  bucket = aws_s3_bucket.report.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "report" {
  bucket = aws_s3_bucket.report.id

  versioning_configuration {
    status = "Enabled" # never suspended (Security Hub CSPM S3.14)
  }
}

# Each page version is a snapshot of who had what, so old versions expire after 1 day.
# S3 counts the day from the overwrite, rounds up to midnight UTC and deletes some time later,
# so an old version can live about 2 days. The empty filter covers config/ (old rosters) too.
resource "aws_s3_bucket_lifecycle_configuration" "report" {
  bucket = aws_s3_bucket.report.id

  rule {
    id     = "expire-noncurrent-after-1-day"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 1
    }

    expiration {
      expired_object_delete_marker = true
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.report]
}

data "aws_iam_policy_document" "report_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.report.arn, "${aws_s3_bucket.report.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  # Nobody but the Lambda replaces or deletes the page. An admin can still edit this policy,
  # so the deny stops accidental overwrites, not a determined admin.
  statement {
    sid       = "SiteWritesOnlyByTheLambda"
    effect    = "Deny"
    actions   = ["s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion"]
    resources = ["${aws_s3_bucket.report.arn}/site/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "ArnNotEquals"
      variable = "aws:PrincipalArn"
      values   = [aws_iam_role.lambda.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "report" {
  bucket     = aws_s3_bucket.report.id
  policy     = data.aws_iam_policy_document.report_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.report]
}
