locals {
  aws_direct_arn = "arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:instance/${var.aws_direct_instance_id}"
}

resource "aws_iam_user" "control" {
  name = "${var.name_prefix}-control-restart"
  path = "/veilway/"
}

data "aws_iam_policy_document" "control" {
  statement {
    sid       = "RestartOnlyAwsDirect"
    effect    = "Allow"
    actions   = ["ec2:RebootInstances"]
    resources = [local.aws_direct_arn]
  }

  statement {
    sid       = "ReadStatusInSelectedRegion"
    effect    = "Allow"
    actions   = ["ec2:DescribeInstanceStatus"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }
}

resource "aws_iam_user_policy" "control" {
  name   = "${var.name_prefix}-control-restart"
  user   = aws_iam_user.control.name
  policy = data.aws_iam_policy_document.control.json
}
