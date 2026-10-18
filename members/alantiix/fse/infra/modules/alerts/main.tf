# Alert topic for the runner host, CloudWatch alarms and batch task events.
#
# Unencrypted on purpose: EventBridge and CloudWatch alarms cannot publish to a
# topic encrypted with the AWS-managed aws/sns key, and a customer-managed key
# costs $1/month for alert text that holds no secrets.
#trivy:ignore:AVD-AWS-0095
resource "aws_sns_topic" "alerts" {
  name = var.topic_name
}

resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn

  # Same-account IAM principals (the runner role) publish through their own
  # identity policies; this policy only adds the two AWS services.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowEventBridgeAndCloudWatch"
        Effect    = "Allow"
        Principal = { Service = ["events.amazonaws.com", "cloudwatch.amazonaws.com"] }
        Action    = "sns:Publish"
        Resource  = aws_sns_topic.alerts.arn
        Condition = {
          StringEquals = { "aws:SourceAccount" = var.account_id }
        }
      },
    ]
  })
}

# AWS mails a confirmation link; the subscription stays pending until clicked.
resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
