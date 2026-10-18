output "file_system_id" {
  value = aws_efs_file_system.cache.id
}

output "file_system_arn" {
  value = aws_efs_file_system.cache.arn
}

output "access_point_id" {
  value = aws_efs_access_point.cache.id
}

output "access_point_arn" {
  value = aws_efs_access_point.cache.arn
}

output "mount_target_ids" {
  value = aws_efs_mount_target.cache[*].id
}
