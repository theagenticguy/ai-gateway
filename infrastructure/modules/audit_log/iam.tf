# =============================================================================
# IAM — Firehose delivery role and least-privilege policies
# =============================================================================

# ------------------------------------------------------------------
# Data sources
# ------------------------------------------------------------------

data "aws_caller_identity" "current" {
  count = var.enable_audit_log ? 1 : 0
}

data "aws_partition" "current" {
  count = var.enable_audit_log ? 1 : 0
}

# ------------------------------------------------------------------
# Firehose IAM Role
# ------------------------------------------------------------------

resource "aws_iam_role" "firehose" {
  count = var.enable_audit_log ? 1 : 0

  name = "${var.project_name}-${var.environment}-audit-firehose"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowFirehoseAssume"
        Effect = "Allow"
        Principal = {
          Service = "firehose.amazonaws.com"
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "sts:ExternalId" = data.aws_caller_identity.current[0].account_id
          }
        }
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-${var.environment}-audit-firehose"
  }
}

# ------------------------------------------------------------------
# S3 delivery policy
# ------------------------------------------------------------------

resource "aws_iam_role_policy" "firehose_s3" {
  count = var.enable_audit_log ? 1 : 0

  name = "s3-delivery"
  role = aws_iam_role.firehose[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowS3Delivery"
        Effect = "Allow"
        Action = [
          "s3:AbortMultipartUpload",
          "s3:GetBucketLocation",
          "s3:GetObject",
          "s3:ListBucket",
          "s3:ListBucketMultipartUploads",
          "s3:PutObject",
        ]
        Resource = [
          aws_s3_bucket.audit[0].arn,
          "${aws_s3_bucket.audit[0].arn}/*",
        ]
      }
    ]
  })
}

# ------------------------------------------------------------------
# Glue catalog access policy (for Parquet format conversion)
# ------------------------------------------------------------------

resource "aws_iam_role_policy" "firehose_glue" {
  count = var.enable_audit_log ? 1 : 0

  name = "glue-schema-access"
  role = aws_iam_role.firehose[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowGlueCatalogAccess"
        Effect = "Allow"
        Action = [
          "glue:GetTable",
          "glue:GetTableVersion",
          "glue:GetTableVersions",
        ]
        Resource = [
          "arn:${data.aws_partition.current[0].partition}:glue:${var.aws_region}:${data.aws_caller_identity.current[0].account_id}:catalog",
          "arn:${data.aws_partition.current[0].partition}:glue:${var.aws_region}:${data.aws_caller_identity.current[0].account_id}:database/${aws_glue_catalog_database.audit[0].name}",
          "arn:${data.aws_partition.current[0].partition}:glue:${var.aws_region}:${data.aws_caller_identity.current[0].account_id}:table/${aws_glue_catalog_database.audit[0].name}/${aws_glue_catalog_table.audit[0].name}",
        ]
      }
    ]
  })
}

# ------------------------------------------------------------------
# CloudWatch Logs policy (for Firehose error logging)
# ------------------------------------------------------------------

resource "aws_iam_role_policy" "firehose_logs" {
  count = var.enable_audit_log ? 1 : 0

  name = "cloudwatch-logs"
  role = aws_iam_role.firehose[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowLogDelivery"
        Effect = "Allow"
        Action = [
          "logs:PutLogEvents",
        ]
        Resource = [
          "${aws_cloudwatch_log_group.firehose[0].arn}:*",
        ]
      }
    ]
  })
}
