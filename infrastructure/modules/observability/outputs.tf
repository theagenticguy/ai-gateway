output "gateway_log_group_name" { value = aws_cloudwatch_log_group.gateway.name }
output "otel_log_group_name" { value = aws_cloudwatch_log_group.otel.name }
output "logs_kms_key_arn" { value = aws_kms_key.logs.arn }
output "gateway_log_group_arn" { value = aws_cloudwatch_log_group.gateway.arn }

output "dashboard_name" {
  description = "Name of the CloudWatch dashboard"
  value       = aws_cloudwatch_dashboard.main.dashboard_name
}

output "alarm_topic_arns" {
  description = "SNS topic ARNs used for alarm notifications"
  value       = local.alarm_topic_arns
}

output "alarm_arns" {
  description = "Map of alarm name to ARN"
  value = merge(
    {
      budget_utilization = aws_cloudwatch_metric_alarm.budget_utilization.arn
      high_error_rate    = aws_cloudwatch_metric_alarm.high_error_rate.arn
      high_p99_latency   = aws_cloudwatch_metric_alarm.high_p99_latency.arn
    },
    { for k, v in aws_cloudwatch_metric_alarm.provider_down : "provider_down_${k}" => v.arn }
  )
}
