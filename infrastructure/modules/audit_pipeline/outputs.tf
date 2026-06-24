output "firehose_stream_name" {
  description = "Name of the audit Firehose delivery stream (gwcore.audit AUDIT_FIREHOSE_STREAM)"
  value       = var.enable_audit_pipeline ? aws_kinesis_firehose_delivery_stream.audit[0].name : ""
}

output "firehose_stream_arn" {
  description = "ARN of the audit Firehose delivery stream (grant handlers firehose:PutRecord)"
  value       = var.enable_audit_pipeline ? aws_kinesis_firehose_delivery_stream.audit[0].arn : ""
}

output "table_bucket_arn" {
  description = "ARN of the S3 Tables bucket holding the audit Iceberg table"
  value       = var.enable_audit_pipeline ? aws_s3tables_table_bucket.audit[0].arn : ""
}

output "table_name" {
  description = "Fully-qualified audit table (namespace.table) for Athena queries"
  value       = var.enable_audit_pipeline ? "${local.namespace}.${local.table}" : ""
}
