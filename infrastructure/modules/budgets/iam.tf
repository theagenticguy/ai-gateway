# =============================================================================
# IAM — Policy for Lambda read/write access to budget tables + KMS
# =============================================================================

data "aws_iam_policy_document" "budget_lambda" {
  count = var.enable_budgets ? 1 : 0

  statement {
    sid    = "DynamoDBReadWrite"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:BatchGetItem",
      "dynamodb:BatchWriteItem",
    ]
    resources = [
      aws_dynamodb_table.budgets[0].arn,
      "${aws_dynamodb_table.budgets[0].arn}/index/*",
      aws_dynamodb_table.usage[0].arn,
      "${aws_dynamodb_table.usage[0].arn}/index/*",
    ]
  }

  statement {
    sid    = "KMSAccess"
    effect = "Allow"
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
    ]
    resources = [
      aws_kms_key.budgets[0].arn,
    ]
  }
}

resource "aws_iam_policy" "budget_lambda" {
  count = var.enable_budgets ? 1 : 0

  name        = "${var.project_name}-${var.environment}-budget-lambda"
  description = "Allows Lambda read/write access to budget and usage DynamoDB tables"
  policy      = data.aws_iam_policy_document.budget_lambda[0].json

  tags = merge(var.tags, {
    Name = "${var.project_name}-${var.environment}-budget-lambda"
  })
}
