output "lambda_function_arn" {
  value = var.enable_cost_attribution ? aws_lambda_function.cost_attribution[0].arn : null
}
output "lambda_function_name" {
  value = var.enable_cost_attribution ? aws_lambda_function.cost_attribution[0].function_name : null
}
