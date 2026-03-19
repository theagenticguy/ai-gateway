resource "aws_cloudwatch_log_group" "gateway" {
  name              = "/ecs/${var.project_name}/gateway"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "otel" {
  name              = "/ecs/${var.project_name}/otel"
  retention_in_days = 30
}
