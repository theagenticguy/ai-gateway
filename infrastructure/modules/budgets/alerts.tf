# =============================================================================
# E.6: Budget Alerts — SNS topic for budget threshold notifications
# =============================================================================

resource "aws_sns_topic" "budget_alerts" {
  count = var.enable_budgets ? 1 : 0

  name              = "${var.project_name}-${var.environment}-budget-alerts"
  kms_master_key_id = aws_kms_key.budgets[0].id

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-budget-alerts"
  })
}

resource "aws_sns_topic_policy" "budget_alerts" {
  count = var.enable_budgets ? 1 : 0

  arn = aws_sns_topic.budget_alerts[0].arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowLambdaPublish"
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.budget_alerts[0].arn
        Condition = {
          ArnLike = {
            "aws:SourceArn" = "arn:aws:lambda:*:${data.aws_caller_identity.current.account_id}:function:${var.project_name}-*"
          }
        }
      }
    ]
  })
}

# IAM policy for Lambdas to publish to the budget alerts SNS topic
data "aws_iam_policy_document" "budget_alerts_publish" {
  count = var.enable_budgets ? 1 : 0

  statement {
    sid    = "SNSPublish"
    effect = "Allow"
    actions = [
      "sns:Publish",
    ]
    resources = [
      aws_sns_topic.budget_alerts[0].arn,
    ]
  }

  statement {
    sid    = "KMSForSNS"
    effect = "Allow"
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:GenerateDataKey*",
    ]
    resources = [
      aws_kms_key.budgets[0].arn,
    ]
  }
}

resource "aws_iam_policy" "budget_alerts_publish" {
  count = var.enable_budgets ? 1 : 0

  name        = "${var.project_name}-${var.environment}-budget-alerts-publish"
  description = "Allows Lambda functions to publish to the budget alerts SNS topic"
  policy      = data.aws_iam_policy_document.budget_alerts_publish[0].json

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-budget-alerts-publish"
  })
}
