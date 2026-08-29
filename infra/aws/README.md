# AWS Direct infrastructure

This Terraform root creates a new dual-stack VPC, public subnet, internet
gateway, restricted security group, encrypted Ubuntu 24.04 EC2 instance, and
Elastic IPv4. It does not connect to the VM or deploy the VPN application.
The security group exposes the Direct VPN on public UDP/1194 and the inter-cloud
OpenVPN transit on UDP/1196 only from the operator-supplied Yandex static IPv4
`/32`. AWS does not expose the Yandex client-ingress port UDP/1195.

State is local by design for the MVP and contains sensitive infrastructure
data. Run with `umask 077`, keep state and `terraform.tfvars` on an encrypted
disk, and never commit or publish them. AWS credentials are read only from the
standard AWS provider credential chain.

The generated ULA is persisted in this root's state. Replacing or losing the
state changes the IPv6 client subnet and requires new profiles.
The same persistent `/48` also supplies separate `/64` networks for multi-hop
clients and the Yandex-to-AWS transit.

The instance explicitly disables automatic public IPv4 assignment at creation,
and the subnet also has `map_public_ip_on_launch` disabled. The Elastic IP is a
separately managed resource. After it is attached, the AWS provider observes
`associate_public_ip_address` as enabled; lifecycle handling ignores only that
post-create observation to avoid a spurious instance replacement.

The EC2 metadata endpoint is enabled in its minimum first-boot configuration
because Ubuntu cloud-init obtains the selected EC2 public SSH key from instance
metadata. IMDSv2 tokens are required, the response hop limit is one, and the
IPv6 endpoint and metadata tags are disabled. The instance has no IAM profile,
so IMDS cannot provide AWS role credentials. The host firewall independently
denies VPN clients access to IPv4 and IPv6 metadata destinations.
