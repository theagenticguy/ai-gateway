output "firehose_stream_name" {
  description = "Name of the Kinesis Firehose delivery stream for audit logs"
  value       = try(aws_kinesis_firehose_delivery_stream.audit[0].name, "")
}

output "firehose_stream_arn" {
  description = "ARN of the Kinesis Firehose delivery stream for audit logs"
  value       = try(aws_kinesis_firehose_delivery_stream.audit[0].arn, "")
}

output "s3_bucket_name" {
  description = "Name of the S3 bucket storing audit log Parquet files"
  value       = try(aws_s3_bucket.audit[0].bucket, "")
}

output "s3_bucket_arn" {
  description = "ARN of the S3 bucket storing audit log Parquet files"
  value       = try(aws_s3_bucket.audit[0].arn, "")
}

output "glue_database_name" {
  description = "Name of the Glue catalog database for audit log queries"
  value       = try(aws_glue_catalog_database.audit[0].name, "")
}

output "glue_table_name" {
  description = "Name of the Glue catalog table for audit log schema"
  value       = try(aws_glue_catalog_table.audit[0].name, "")
}
