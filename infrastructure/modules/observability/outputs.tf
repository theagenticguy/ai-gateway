output "gateway_log_group_name" { value = aws_cloudwatch_log_group.gateway.name }
output "otel_log_group_name" { value = aws_cloudwatch_log_group.otel.name }
output "logs_kms_key_arn" { value = aws_kms_key.logs.arn }
output "gateway_log_group_arn" { value = aws_cloudwatch_log_group.gateway.arn }
