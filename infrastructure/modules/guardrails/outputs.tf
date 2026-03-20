output "guardrail_id" {
  description = "ID of the Bedrock Guardrail"
  value       = var.enable_guardrails ? aws_bedrock_guardrail.this[0].guardrail_id : null
}

output "guardrail_arn" {
  description = "ARN of the Bedrock Guardrail"
  value       = var.enable_guardrails ? aws_bedrock_guardrail.this[0].guardrail_arn : null
}

output "guardrail_version" {
  description = "Published version number of the Bedrock Guardrail"
  value       = var.enable_guardrails ? aws_bedrock_guardrail_version.this[0].version : null
}
