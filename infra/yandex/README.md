# Yandex Direct infrastructure

This Terraform root creates a new IPv4 VPC, subnet, restricted security group,
reserved public IPv4, and Ubuntu 24.04 VM. It does not connect to the VM or
deploy the VPN application. Yandex credentials are read only from the standard
provider authentication mechanisms.

The security group exposes Direct on UDP/1194 and the multi-hop client ingress
on UDP/1195. It does not expose the AWS transit port UDP/1196. The host
firewall, not Terraform or Docker, owns forwarding and enforces that the
multi-hop client pool has no Yandex WAN fallback.

State is local by design for the MVP and contains sensitive infrastructure
data. Run with `umask 077`, keep state and `terraform.tfvars` on an encrypted
disk, and never commit or publish them.
