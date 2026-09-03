# AWS management IAM root

This root creates the dedicated IAM user and inline least-privilege policy. It
does **not** create `aws_iam_access_key`, so no secret is written to Terraform
state. `RebootInstances` is restricted to the exact `aws-direct` ARN;
`DescribeInstanceStatus` is restricted to the configured region.

Create the access key manually as a separately approved operation, place it
only in the ignored local `.env`, and never paste it into Terraform variables.
