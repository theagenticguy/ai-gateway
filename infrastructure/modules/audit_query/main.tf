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
# Audit Query Surface — Athena over the S3 Tables Iceberg audit trail
# =============================================================================
# Read path for control_plane.audit_events (ADR-016/017). Provisions:
#   - an S3 results bucket (encrypted, private, versioned, lifecycle-expiry),
#   - an Athena workgroup that enforces the results location + SSE,
#   - the canned audit named queries (SQL single-sourced from queries/*.sql),
#   - a least-privilege IAM managed policy the GET /audit Lambda role attaches.
#
# BOOTSTRAP (one-time, per account per Region) — NOT a first-class TF resource
# in the AWS provider today (research gotcha: terraform_integration_toggle,
# UNVERIFIED whether aws_s3tables_* exposes an "enable integration" resource in
# v6.53.0). Before Athena can see the S3 Tables, enable "Integration with AWS
# analytics services" from the S3 console (Table buckets → Enable integration)
# or via the Lake Formation / Glue API. That creates the top-level Glue catalog
# `s3tablescatalog`, the `S3TablesRoleForLakeFormation` service role, and
# auto-populates each table bucket as a child sub-catalog
# `s3tablescatalog/<bucket>`, namespaces → Glue databases, tables → Glue tables.
# ai-gateway runs in us-east-1 (a core Region) so IAM access mode suffices; the
# Lake Formation grant below is only required outside the core Regions.
# =============================================================================

locals {
  results_bucket = "${var.project_name}-${var.environment}-athena-results"
  workgroup_name = "${var.project_name}-${var.environment}-audit"

  # Fully-qualified Athena catalog for the S3 Tables child sub-catalog. The
  # named queries embed this three-part name; the Lambda passes the same via the
  # StartQueryExecution QueryExecutionContext.
  child_catalog = "s3tablescatalog/${var.audit_table_bucket_name}"

  # SQL is single-sourced from queries/*.sql. The files carry a <BUCKET>
  # placeholder (so they read cleanly as standalone docs); we substitute the
  # real table-bucket name here so the stored named query is self-contained and
  # resolves the S3 Tables sub-catalog (aws_athena_named_query has NO catalog arg).
  queries = {
    audit_by_team_period     = "audit_by_team_period.sql"
    audit_denials            = "audit_denials.sql"
    audit_mutations_by_actor = "audit_mutations_by_actor.sql"
    audit_recent             = "audit_recent.sql"
  }
}

# -----------------------------------------------------------------------------
# Athena query-results bucket (encrypted, private, versioned, expiring)
# -----------------------------------------------------------------------------

#checkov:skip=CKV2_AWS_62:Event notifications not required for a transient query-results bucket
#checkov:skip=CKV_AWS_18:Access logging not required for an internal query-results bucket
resource "aws_s3_bucket" "results" {
  count         = var.enable_audit_query ? 1 : 0
  bucket        = local.results_bucket
  force_destroy = false
}

resource "aws_s3_bucket_server_side_encryption_configuration" "results" {
  count  = var.enable_audit_query ? 1 : 0
  bucket = aws_s3_bucket.results[0].id
  rule {
    apply_server_side_encryption_by_default {
      # SSE-S3 (AES256) keeps the module self-contained without a KMS dependency
      # and matches the audit_pipeline error bucket. NOTE: Athena workgroups
      # using SSE-KMS block DML on S3 Tables; SSE-S3 avoids that and is fine for
      # a read-only audit surface (research gotcha: unsupported_ddl_dml).
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "results" {
  count                   = var.enable_audit_query ? 1 : 0
  bucket                  = aws_s3_bucket.results[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "results" {
  count  = var.enable_audit_query ? 1 : 0
  bucket = aws_s3_bucket.results[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "results" {
  count  = var.enable_audit_query ? 1 : 0
  bucket = aws_s3_bucket.results[0].id

  rule {
    id     = "expire-query-results"
    status = "Enabled"
    filter {}
    expiration {
      days = var.results_expiry_days
    }
    # Query result reuse is unsupported for S3 Tables, so results are disposable;
    # expire noncurrent versions promptly too.
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# -----------------------------------------------------------------------------
# Athena workgroup — owns the results location + SSE + engine enforcement
# -----------------------------------------------------------------------------

resource "aws_athena_workgroup" "audit" {
  count         = var.enable_audit_query ? 1 : 0
  name          = local.workgroup_name
  description   = "Read-only audit queries over control_plane.audit_events (S3 Tables Iceberg)"
  state         = "ENABLED"
  force_destroy = true

  configuration {
    # Enforce the workgroup config so callers cannot override the results
    # location or SSE — REQUIRED for a governed audit surface.
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true

    result_configuration {
      output_location = "s3://${aws_s3_bucket.results[0].bucket}/audit-output/"
      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}

# -----------------------------------------------------------------------------
# Named queries — the canned audit SQL (single-sourced from queries/*.sql)
# -----------------------------------------------------------------------------
# aws_athena_named_query has `database` + `workgroup` but NO catalog arg, so the
# S3 Tables child catalog is fully-qualified INSIDE each query string (the
# <BUCKET> placeholder in the .sql is replaced with the real bucket name).

resource "aws_athena_named_query" "audit" {
  for_each = var.enable_audit_query ? local.queries : {}

  name      = each.key
  workgroup = aws_athena_workgroup.audit[0].name
  database  = var.namespace # documentation; the SQL below resolves the sub-catalog
  query = replace(
    file("${path.module}/queries/${each.value}"),
    "<BUCKET>",
    var.audit_table_bucket_name,
  )
}

# -----------------------------------------------------------------------------
# IAM — least-privilege managed policy for the query principal (the GET /audit
# Lambda role attaches this). IAM access mode (us-east-1 is a core Region).
# -----------------------------------------------------------------------------

resource "aws_iam_policy" "audit_query" {
  count       = var.enable_audit_query ? 1 : 0
  name        = "${var.project_name}-${var.environment}-audit-query"
  description = "Run the audit Athena workgroup + read the S3 Tables audit trail"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AthenaWorkgroup"
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StopQueryExecution",
          "athena:GetWorkGroup",
          "athena:ListNamedQueries",
          "athena:GetNamedQuery",
        ]
        Resource = "arn:aws:athena:${var.aws_region}:${var.account_id}:workgroup/${local.workgroup_name}"
      },
      {
        Sid    = "S3TablesRead"
        Effect = "Allow"
        Action = [
          "s3tables:GetTableBucket",
          "s3tables:GetNamespace",
          "s3tables:ListNamespaces",
          "s3tables:GetTable",
          "s3tables:ListTables",
          "s3tables:GetTableData",
          "s3tables:GetTableMetadataLocation",
        ]
        Resource = [
          var.audit_table_bucket_arn,
          "${var.audit_table_bucket_arn}/*",
        ]
      },
      {
        Sid    = "GlueCatalogRead"
        Effect = "Allow"
        Action = [
          "glue:GetCatalog",
          "glue:GetDatabase",
          "glue:GetDatabases",
          "glue:GetTable",
          "glue:GetTables",
          "glue:GetPartitions",
        ]
        # The S3 Tables federation reaches the tables through the default Glue
        # catalog + the s3tablescatalog child; scope to the account's catalog and
        # the control_plane database/tables.
        Resource = [
          "arn:aws:glue:${var.aws_region}:${var.account_id}:catalog",
          "arn:aws:glue:${var.aws_region}:${var.account_id}:catalog/s3tablescatalog",
          "arn:aws:glue:${var.aws_region}:${var.account_id}:catalog/s3tablescatalog/${var.audit_table_bucket_name}",
          "arn:aws:glue:${var.aws_region}:${var.account_id}:database/${var.namespace}",
          "arn:aws:glue:${var.aws_region}:${var.account_id}:table/${var.namespace}/*",
        ]
      },
      {
        Sid    = "AthenaResultsBucket"
        Effect = "Allow"
        Action = [
          "s3:GetBucketLocation",
          "s3:ListBucket",
          "s3:GetObject",
          "s3:PutObject",
        ]
        Resource = [
          aws_s3_bucket.results[0].arn,
          "${aws_s3_bucket.results[0].arn}/*",
        ]
      },
    ]
  })
}

# -----------------------------------------------------------------------------
# Lake Formation grant (OPTIONAL — only required OUTSIDE the core Regions).
# -----------------------------------------------------------------------------
# ai-gateway is us-east-1 (a core Region) → IAM access mode above suffices and
# this block is intentionally commented. When deploying to a non-core Region,
# grant the query principal Super (["ALL"]) on the S3 Tables CHILD catalog,
# addressed as "<account_id>:s3tablescatalog/<bucket>".
#
# UNVERIFIED: confirm child-catalog block shape via terraform plan
# (research gotcha lakeformation_child_catalog_tf_block). The CLI grant
# (Resource.Catalog.Id = "<acct>:s3tablescatalog/<bucket>", Permissions ["ALL"])
# is confirmed by AWS docs; the exact aws_lakeformation_permissions field wiring
# for the child catalog id is NOT field-verified for provider v6.x.
#
# resource "aws_lakeformation_permissions" "audit_catalog_super" {
#   count       = var.enable_audit_query ? 1 : 0
#   principal   = var.query_principal_arn
#   permissions = ["ALL"]
#   catalog_id  = "${var.account_id}:s3tablescatalog/${var.audit_table_bucket_name}"
#   catalog_resource = true
# }
