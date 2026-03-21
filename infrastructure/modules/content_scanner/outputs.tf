output "function_url" {
  description = "Lambda Function URL for the content scanner"
  value       = var.enable_content_scanner ? aws_lambda_function_url.content_scanner[0].function_url : null
}

output "function_arn" {
  description = "ARN of the content scanner Lambda function"
  value       = var.enable_content_scanner ? aws_lambda_function.content_scanner[0].arn : null
}
