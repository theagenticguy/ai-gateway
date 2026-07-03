output "workgroup_name" {
  description = "Name of the audit Athena workgroup (set as AUDIT_ATHENA_WORKGROUP on the query Lambda)."
  value       = var.enable_audit_query ? aws_athena_workgroup.audit[0].name : ""
}

output "results_bucket" {
  description = "Name of the Athena query-results S3 bucket."
  value       = var.enable_audit_query ? aws_s3_bucket.results[0].bucket : ""
}

output "results_bucket_arn" {
  description = "ARN of the Athena query-results S3 bucket."
  value       = var.enable_audit_query ? aws_s3_bucket.results[0].arn : ""
}

output "child_catalog" {
  description = "Fully-qualified S3 Tables child catalog (s3tablescatalog/<bucket>) for the QueryExecutionContext Catalog field."
  value       = var.enable_audit_query ? local.child_catalog : ""
}

output "database" {
  description = "Glue database (= S3 Tables namespace) holding the audit tables."
  value       = var.namespace
}

output "audit_query_policy_arn" {
  description = "ARN of the least-privilege IAM policy to attach to the GET /audit Lambda role."
  value       = var.enable_audit_query ? aws_iam_policy.audit_query[0].arn : ""
}

output "named_query_ids" {
  description = "Map of named-query name → Athena named-query id."
  value       = var.enable_audit_query ? { for k, q in aws_athena_named_query.audit : k => q.id } : {}
}
