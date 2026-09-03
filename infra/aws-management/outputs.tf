output "iam_user_name" {
  value       = aws_iam_user.control.name
  description = "IAM user for manually creating the runtime access key"
}

output "allowed_instance_arn" {
  value       = local.aws_direct_arn
  description = "The only resource allowed for RebootInstances"
}
