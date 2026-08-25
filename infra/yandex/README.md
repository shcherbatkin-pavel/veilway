# Yandex Direct infrastructure

This Terraform root creates a new IPv4 VPC, subnet, restricted security group,
reserved public IPv4, and Ubuntu 24.04 VM. It does not connect to the VM or
deploy the VPN application. Yandex credentials are read only from the standard
provider authentication mechanisms.

State is local by design for the MVP and contains sensitive infrastructure
data. Run with `umask 077`, keep state and `terraform.tfvars` on an encrypted
disk, and never commit or publish them.
