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
# Audit Log Pipeline — Kinesis Firehose -> S3 (Parquet) with Glue Catalog
# =============================================================================

# ------------------------------------------------------------------
# S3 Bucket — audit log storage
# ------------------------------------------------------------------

#checkov:skip=CKV_AWS_145:Using SSE-S3 (AES256) to keep module self-contained without KMS dependency
#checkov:skip=CKV_AWS_144:Cross-region replication not required for audit logs
#checkov:skip=CKV2_AWS_62:Event notifications not required for audit pipeline
#checkov:skip=CKV_AWS_21:Versioning disabled — audit logs are append-only
#checkov:skip=CKV_AWS_18:Access logging not required for audit logs bucket (it IS the log destination)
resource "aws_s3_bucket" "audit" {
  count = var.enable_audit_log ? 1 : 0

  bucket        = "${var.project_name}-${var.environment}-audit-logs"
  force_destroy = false

  tags = {
    Name = "${var.project_name}-${var.environment}-audit-logs"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "audit" {
  count = var.enable_audit_log ? 1 : 0

  bucket = aws_s3_bucket.audit[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

#checkov:skip=CKV_AWS_21:Versioning disabled — audit logs are append-only
resource "aws_s3_bucket_versioning" "audit" {
  count = var.enable_audit_log ? 1 : 0

  bucket = aws_s3_bucket.audit[0].id

  versioning_configuration {
    status = "Disabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "audit" {
  count = var.enable_audit_log ? 1 : 0

  bucket = aws_s3_bucket.audit[0].id

  rule {
    id     = "audit-log-lifecycle"
    status = "Enabled"

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }

    expiration {
      days = 365
    }
  }
}

resource "aws_s3_bucket_public_access_block" "audit" {
  count = var.enable_audit_log ? 1 : 0

  bucket = aws_s3_bucket.audit[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ------------------------------------------------------------------
# Glue Catalog — database and table for Athena queries
# ------------------------------------------------------------------

resource "aws_glue_catalog_database" "audit" {
  count = var.enable_audit_log ? 1 : 0

  name = "${var.project_name}_${var.environment}_audit"
}

resource "aws_glue_catalog_table" "audit" {
  count = var.enable_audit_log ? 1 : 0

  database_name = aws_glue_catalog_database.audit[0].name
  name          = "gateway_audit_log"
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    "classification" = "parquet"
    "EXTERNAL"       = "TRUE"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.audit[0].bucket}/audit/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"

      parameters = {
        "serialization.format" = "1"
      }
    }

    columns {
      name = "team"
      type = "string"
    }

    columns {
      name = "user_id"
      type = "string"
    }

    columns {
      name = "model"
      type = "string"
    }

    columns {
      name = "provider"
      type = "string"
    }

    columns {
      name = "prompt_tokens"
      type = "int"
    }

    columns {
      name = "completion_tokens"
      type = "int"
    }

    columns {
      name = "total_tokens"
      type = "int"
    }

    columns {
      name = "cost_usd"
      type = "double"
    }

    columns {
      name = "cache_read_tokens"
      type = "int"
    }

    columns {
      name = "cache_savings_usd"
      type = "double"
    }

    columns {
      name = "latency_ms"
      type = "int"
    }

    columns {
      name = "status"
      type = "string"
    }

    columns {
      name = "correlation_id"
      type = "string"
    }

    columns {
      name = "request_timestamp"
      type = "string"
    }
  }

  partition_keys {
    name = "year"
    type = "string"
  }

  partition_keys {
    name = "month"
    type = "string"
  }

  partition_keys {
    name = "day"
    type = "string"
  }
}

# ------------------------------------------------------------------
# CloudWatch Log Group — Firehose delivery error logs
# ------------------------------------------------------------------

#checkov:skip=CKV_AWS_158:KMS encryption not required for Firehose error logs
#checkov:skip=CKV_AWS_338:30-day retention sufficient for delivery error logs
resource "aws_cloudwatch_log_group" "firehose" {
  count = var.enable_audit_log ? 1 : 0

  name              = "/aws/firehose/${var.project_name}-${var.environment}-audit"
  retention_in_days = 30

  tags = {
    Name = "${var.project_name}-${var.environment}-firehose-audit"
  }
}

resource "aws_cloudwatch_log_stream" "firehose_s3" {
  count = var.enable_audit_log ? 1 : 0

  name           = "S3Delivery"
  log_group_name = aws_cloudwatch_log_group.firehose[0].name
}

# ------------------------------------------------------------------
# Kinesis Firehose — delivery stream with Parquet format conversion
# ------------------------------------------------------------------

#checkov:skip=CKV_AWS_252:Firehose encryption managed by S3 SSE
resource "aws_kinesis_firehose_delivery_stream" "audit" {
  count = var.enable_audit_log ? 1 : 0

  name        = "${var.project_name}-${var.environment}-audit-stream"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn   = aws_iam_role.firehose[0].arn
    bucket_arn = aws_s3_bucket.audit[0].arn

    # Hive-style partitioning for year/month/day
    prefix              = "audit/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/"
    error_output_prefix = "errors/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/!{firehose:error-output-type}/"

    # Buffering — minimum 64 MB for format conversion
    buffering_size     = 128
    buffering_interval = 300

    # Parquet format conversion via Glue schema
    data_format_conversion_configuration {
      enabled = true

      input_format_configuration {
        deserializer {
          open_x_json_ser_de {}
        }
      }

      output_format_configuration {
        serializer {
          parquet_ser_de {
            compression = "SNAPPY"
          }
        }
      }

      schema_configuration {
        role_arn      = aws_iam_role.firehose[0].arn
        database_name = aws_glue_catalog_database.audit[0].name
        table_name    = aws_glue_catalog_table.audit[0].name
        region        = var.aws_region
      }
    }

    # Error logging
    cloudwatch_logging_options {
      enabled         = true
      log_group_name  = aws_cloudwatch_log_group.firehose[0].name
      log_stream_name = aws_cloudwatch_log_stream.firehose_s3[0].name
    }
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-audit-stream"
  }
}
