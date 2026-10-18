output "topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "topic_name" {
  value = aws_sns_topic.alerts.name
}
