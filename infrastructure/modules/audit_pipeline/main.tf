terraform {
  required_version = "~> 1.14"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.22"
    }
  }
}

# =============================================================================
# Audit Pipeline — Firehose → Apache Iceberg on S3 Tables (ADR-016)
# =============================================================================
# Successor to the modules/audit_log Parquet+Glue pipeline. Firehose writes
# directly to an Iceberg table in an S3 Tables bucket, giving ACID commits,
# automatic compaction, snapshot expiration, and schema evolution — queried by
# Athena/Spark with no Glue crawler. gwcore.audit puts JSON records onto this
# stream; the control-plane audit explorer reads the Iceberg table.
# =============================================================================

locals {
  name      = "${var.project_name}-${var.environment}-audit"
  namespace = "control_plane"
  table     = "audit_events"
}

# -----------------------------------------------------------------------------
# S3 Tables bucket + namespace + Iceberg table
# -----------------------------------------------------------------------------

resource "aws_s3tables_table_bucket" "audit" {
  count = var.enable_audit_pipeline ? 1 : 0
  name  = local.name
}

resource "aws_s3tables_namespace" "audit" {
  count            = var.enable_audit_pipeline ? 1 : 0
  namespace        = local.namespace
  table_bucket_arn = aws_s3tables_table_bucket.audit[0].arn
}

resource "aws_s3tables_table" "audit" {
  count            = var.enable_audit_pipeline ? 1 : 0
  name             = local.table
  namespace        = aws_s3tables_namespace.audit[0].namespace
  table_bucket_arn = aws_s3tables_table_bucket.audit[0].arn
  format           = "ICEBERG"
  # S3 Tables runs Iceberg compaction + snapshot management on managed defaults
  # at the table-bucket level, which is exactly the small-files remediation the
  # Parquet+Glue path lacked. Override via aws_s3tables_table_bucket_maintenance_
  # configuration only if the defaults prove insufficient.
}

# -----------------------------------------------------------------------------
# Firehose error bucket (records that fail delivery land here)
# -----------------------------------------------------------------------------

#checkov:skip=CKV_AWS_145:SSE-S3 (AES256) keeps the module self-contained without a KMS dependency
#checkov:skip=CKV2_AWS_62:Event notifications not required for the error bucket
#checkov:skip=CKV_AWS_18:This is an internal error-spillover bucket, not a primary data store
#checkov:skip=CKV2_AWS_61:Lifecycle handled by the 30-day expiry rule below
resource "aws_s3_bucket" "errors" {
  count         = var.enable_audit_pipeline ? 1 : 0
  bucket        = "${local.name}-firehose-errors"
  force_destroy = false
}

resource "aws_s3_bucket_server_side_encryption_configuration" "errors" {
  count  = var.enable_audit_pipeline ? 1 : 0
  bucket = aws_s3_bucket.errors[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "errors" {
  count                   = var.enable_audit_pipeline ? 1 : 0
  bucket                  = aws_s3_bucket.errors[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "errors" {
  count  = var.enable_audit_pipeline ? 1 : 0
  bucket = aws_s3_bucket.errors[0].id
  rule {
    id     = "expire-errors"
    status = "Enabled"
    filter {}
    expiration {
      days = 30
    }
  }
}

# -----------------------------------------------------------------------------
# Firehose delivery role
# -----------------------------------------------------------------------------

data "aws_iam_policy_document" "firehose_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["firehose.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "firehose" {
  count              = var.enable_audit_pipeline ? 1 : 0
  name               = "${local.name}-firehose"
  assume_role_policy = data.aws_iam_policy_document.firehose_assume.json
}

data "aws_iam_policy_document" "firehose" {
  # S3 Tables (Iceberg) write + catalog access.
  statement {
    effect = "Allow"
    actions = [
      "s3tables:GetTableBucket",
      "s3tables:GetNamespace",
      "s3tables:GetTable",
      "s3tables:GetTableData",
      "s3tables:PutTableData",
      "s3tables:UpdateTableMetadataLocation",
      "s3tables:GetTableMetadataLocation",
    ]
    resources = [
      aws_s3tables_table_bucket.audit[0].arn,
      "${aws_s3tables_table_bucket.audit[0].arn}/*",
    ]
  }
  # Glue catalog federation for the S3 Tables catalog.
  statement {
    effect    = "Allow"
    actions   = ["glue:GetTable", "glue:GetDatabase", "glue:UpdateTable"]
    resources = ["*"]
  }
  # Error-spillover bucket.
  statement {
    effect    = "Allow"
    actions   = ["s3:PutObject", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.errors[0].arn, "${aws_s3_bucket.errors[0].arn}/*"]
  }
  # Firehose's own CloudWatch logging.
  statement {
    effect    = "Allow"
    actions   = ["logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:*:log-group:/aws/firehose/${local.name}:*"]
  }
}

resource "aws_iam_role_policy" "firehose" {
  count  = var.enable_audit_pipeline ? 1 : 0
  name   = "firehose-iceberg"
  role   = aws_iam_role.firehose[0].id
  policy = data.aws_iam_policy_document.firehose.json
}

# -----------------------------------------------------------------------------
# Firehose CloudWatch log group
# -----------------------------------------------------------------------------

#checkov:skip=CKV_AWS_158:CloudWatch log encryption via KMS is out of scope for this self-contained module
resource "aws_cloudwatch_log_group" "firehose" {
  count             = var.enable_audit_pipeline ? 1 : 0
  name              = "/aws/firehose/${local.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_stream" "firehose" {
  count          = var.enable_audit_pipeline ? 1 : 0
  name           = "iceberg-delivery"
  log_group_name = aws_cloudwatch_log_group.firehose[0].name
}

# -----------------------------------------------------------------------------
# Firehose delivery stream → Iceberg
# -----------------------------------------------------------------------------

resource "aws_kinesis_firehose_delivery_stream" "audit" {
  count       = var.enable_audit_pipeline ? 1 : 0
  name        = local.name
  destination = "iceberg"

  iceberg_configuration {
    role_arn           = aws_iam_role.firehose[0].arn
    catalog_arn        = "arn:aws:glue:${var.aws_region}:${var.account_id}:catalog"
    buffering_size     = 5
    buffering_interval = 60

    destination_table_configuration {
      database_name = local.namespace
      table_name    = local.table
    }

    s3_configuration {
      role_arn   = aws_iam_role.firehose[0].arn
      bucket_arn = aws_s3_bucket.errors[0].arn
    }

    cloudwatch_logging_options {
      enabled         = true
      log_group_name  = aws_cloudwatch_log_group.firehose[0].name
      log_stream_name = aws_cloudwatch_log_stream.firehose[0].name
    }
  }
}
