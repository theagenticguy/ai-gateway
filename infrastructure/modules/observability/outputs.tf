output "gateway_log_group_name" {
  description = "Name of the gateway CloudWatch log group"
  value       = aws_cloudwatch_log_group.gateway.name
}

output "otel_log_group_name" {
  description = "Name of the OpenTelemetry CloudWatch log group"
  value       = aws_cloudwatch_log_group.otel.name
}

output "logs_kms_key_arn" {
  description = "ARN of the KMS key used for CloudWatch Logs encryption"
  value       = aws_kms_key.logs.arn
}
