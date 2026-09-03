variable "aws_account_id" {
  type        = string
  description = "AWS account containing aws-direct"
}

variable "aws_region" {
  type        = string
  description = "The single region containing aws-direct"
}

variable "aws_direct_instance_id" {
  type        = string
  description = "The only EC2 instance this IAM user may reboot"
}

variable "name_prefix" {
  type        = string
  default     = "veilway"
  description = "IAM resource name prefix"
}
