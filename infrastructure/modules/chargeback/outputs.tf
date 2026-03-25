output "report_bucket" {
  description = "Name of the S3 bucket storing chargeback reports"
  value       = var.enable_chargeback ? aws_s3_bucket.reports[0].id : null
}

output "report_bucket_arn" {
  description = "ARN of the S3 bucket storing chargeback reports"
  value       = var.enable_chargeback ? aws_s3_bucket.reports[0].arn : null
}

output "state_machine_arn" {
  description = "ARN of the Step Functions state machine"
  value       = var.enable_chargeback ? aws_sfn_state_machine.chargeback[0].arn : null
}

output "lambda_arn" {
  description = "ARN of the chargeback report Lambda function"
  value       = var.enable_chargeback ? aws_lambda_function.chargeback[0].arn : null
}

output "lambda_function_name" {
  description = "Name of the chargeback report Lambda function"
  value       = var.enable_chargeback ? aws_lambda_function.chargeback[0].function_name : null
}
