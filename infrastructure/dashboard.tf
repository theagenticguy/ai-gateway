# -----------------------------------------------------------------------------
# CloudWatch Saved Queries — AI Gateway Operational Visibility
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_query_definition" "requests_per_hour" {
  name = "${var.project_name}/requests-per-hour-by-provider"

  log_group_names = [aws_cloudwatch_log_group.gateway.name]

  query_string = <<-EOF
    fields @timestamp, @message
    | filter ispresent(responseTime)
    | stats count(*) as requests by bin(1h), `req.headers.x-portkey-provider` as provider
    | sort bin(1h) desc
  EOF
}

resource "aws_cloudwatch_query_definition" "error_rate" {
  name = "${var.project_name}/error-rate-by-provider"

  log_group_names = [aws_cloudwatch_log_group.gateway.name]

  query_string = <<-EOF
    fields @timestamp, @message
    | filter ispresent(res.statusCode)
    | stats count(*) as total,
            sum(res.statusCode >= 400) as errors,
            (sum(res.statusCode >= 400) / count(*)) * 100 as error_pct
      by `req.headers.x-portkey-provider` as provider
    | sort error_pct desc
  EOF
}

resource "aws_cloudwatch_query_definition" "latency_percentiles" {
  name = "${var.project_name}/latency-percentiles-by-provider"

  log_group_names = [aws_cloudwatch_log_group.gateway.name]

  query_string = <<-EOF
    fields @timestamp, responseTime
    | filter ispresent(responseTime)
    | stats pct(responseTime, 50) as p50,
            pct(responseTime, 95) as p95,
            pct(responseTime, 99) as p99,
            avg(responseTime) as avg_ms
      by `req.headers.x-portkey-provider` as provider
    | sort p99 desc
  EOF
}

resource "aws_cloudwatch_query_definition" "requests_by_endpoint" {
  name = "${var.project_name}/requests-by-endpoint"

  log_group_names = [aws_cloudwatch_log_group.gateway.name]

  query_string = <<-EOF
    fields @timestamp, req.url
    | filter ispresent(req.url)
    | stats count(*) as requests by `req.url` as endpoint
    | sort requests desc
    | limit 20
  EOF
}

# -----------------------------------------------------------------------------
# CloudWatch Dashboard — AI Gateway Operational Overview
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.project_name}-${var.environment}"

  dashboard_body = jsonencode({
    widgets = [
      # Row 1, Left: Requests per hour by provider
      {
        type   = "log"
        x      = 0
        y      = 0
        width  = 12
        height = 6

        properties = {
          query   = "SOURCE '${aws_cloudwatch_log_group.gateway.name}' | fields @timestamp, @message | filter ispresent(responseTime) | stats count(*) as requests by bin(1h), `req.headers.x-portkey-provider` as provider | sort bin(1h) desc"
          region  = var.aws_region
          stacked = false
          view    = "timeSeries"
          title   = "Requests per Hour by Provider"
        }
      },

      # Row 1, Right: Error rate by provider
      {
        type   = "log"
        x      = 12
        y      = 0
        width  = 12
        height = 6

        properties = {
          query   = "SOURCE '${aws_cloudwatch_log_group.gateway.name}' | fields @timestamp, @message | filter ispresent(res.statusCode) | stats count(*) as total, sum(res.statusCode >= 400) as errors, (sum(res.statusCode >= 400) / count(*)) * 100 as error_pct by `req.headers.x-portkey-provider` as provider | sort error_pct desc"
          region  = var.aws_region
          stacked = false
          view    = "table"
          title   = "Error Rate by Provider"
        }
      },

      # Row 2, Left: Latency percentiles by provider
      {
        type   = "log"
        x      = 0
        y      = 6
        width  = 12
        height = 6

        properties = {
          query   = "SOURCE '${aws_cloudwatch_log_group.gateway.name}' | fields @timestamp, responseTime | filter ispresent(responseTime) | stats pct(responseTime, 50) as p50, pct(responseTime, 95) as p95, pct(responseTime, 99) as p99, avg(responseTime) as avg_ms by `req.headers.x-portkey-provider` as provider | sort p99 desc"
          region  = var.aws_region
          stacked = false
          view    = "table"
          title   = "Latency Percentiles by Provider (ms)"
        }
      },

      # Row 2, Right: Top endpoints by request count
      {
        type   = "log"
        x      = 12
        y      = 6
        width  = 12
        height = 6

        properties = {
          query   = "SOURCE '${aws_cloudwatch_log_group.gateway.name}' | fields @timestamp, req.url | filter ispresent(req.url) | stats count(*) as requests by `req.url` as endpoint | sort requests desc | limit 20"
          region  = var.aws_region
          stacked = false
          view    = "table"
          title   = "Top Endpoints by Request Count"
        }
      }

      # -----------------------------------------------------------------
      # Future metric widgets (PENDING: requires cost-visibility pipeline)
      # -----------------------------------------------------------------
      # When the AIGateway custom metric namespace is populated, add:
      #
      # {
      #   type   = "metric"
      #   x      = 0
      #   y      = 12
      #   width  = 12
      #   height = 6
      #   properties = {
      #     metrics = [
      #       ["AIGateway", "TokensUsed", "Provider", "bedrock"],
      #       ["AIGateway", "TokensUsed", "Provider", "openai"]
      #     ]
      #     period = 300
      #     stat   = "Sum"
      #     region = var.aws_region
      #     title  = "Token Usage by Provider"
      #   }
      # },
      #
      # {
      #   type   = "metric"
      #   x      = 12
      #   y      = 12
      #   width  = 12
      #   height = 6
      #   properties = {
      #     metrics = [
      #       ["AIGateway", "EstimatedCostUsd", "Provider", "bedrock"],
      #       ["AIGateway", "EstimatedCostUsd", "Provider", "openai"]
      #     ]
      #     period = 300
      #     stat   = "Sum"
      #     region = var.aws_region
      #     title  = "Estimated Cost by Provider (USD)"
      #   }
      # }
    ]
  })
}
