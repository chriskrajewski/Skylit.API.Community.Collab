output "repository_name" {
  value = aws_ecr_repository.this.name
}

output "repository_arn" {
  value = aws_ecr_repository.this.arn
}

output "repository_url" {
  value = aws_ecr_repository.this.repository_url
}

output "scan_on_push" {
  value = aws_ecr_repository.this.image_scanning_configuration[0].scan_on_push
}
