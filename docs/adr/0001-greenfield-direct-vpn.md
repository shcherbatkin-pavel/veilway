# ADR 0001: Greenfield Direct VPN architecture

- Status: Accepted for implementation
- Date: 2026-08-20

## Context

Veilway needs internet-only VPN access for one Ubuntu laptop and one iPhone,
with Yandex Cloud and AWS as independent direct egress providers. The service
will run on new dedicated VMs. Old audit reports remain private and are not
deployment inputs.

Sanitized audit conclusions showed the same operational pattern on both old
hosts: IPv4 forwarding was enabled, reverse-path filtering was loose, IPv6
forwarding was disabled, and Docker and NAT rules shared the firewall. These
facts do not need to be reproduced, but they demonstrate that forwarding,
reverse-path filtering, and firewall ownership must be explicit and testable.

Yandex Cloud VPC currently provides only IPv4 networking. AWS VPC supports a
dual-stack subnet and public IPv6 for EC2. OpenVPN Access Server in Docker is
not selected because the community OpenVPN daemon provides the required
protocol without licensing or the Access Server Docker IPv6 limitations.

## Decision

Use two new Ubuntu 24.04 LTS VMs created by independent Terraform roots. An
explicit Ansible playbook configures each VM and deploys an OpenVPN 2.6 and
Unbound Compose stack. Terraform never runs remote provisioners and never
contains VPN credentials.

The containers use host networking. No ports are published through a Docker
bridge, so Docker does not add a second DNAT or forwarding policy. Ansible owns
one complete nftables ruleset and the required sysctl settings. The OpenVPN
container gets `/dev/net/tun`, `NET_ADMIN`, `SETUID`/`SETGID` for its privilege
drop, and `KILL` so PID 1 can forward stop signals afterward. It drops every
other capability and uses a read-only root filesystem.

Both nodes listen for OpenVPN/UDP on port 1194 over their static public IPv4.
UDP/1195 on Yandex and UDP/1196 on AWS are reserved for future multi-hop use but
remain closed. SSH is allowed only from an operator-provided CIDR list.

AWS enables only the minimum IMDS configuration needed for Ubuntu cloud-init
to install the selected EC2 public SSH key on first boot: IMDSv2 tokens are
required, the response hop limit is one, and metadata tags and the IPv6
endpoint are disabled. The VM has no IAM profile. This host-local bootstrap
path does not replace the separate firewall rule that denies forwarded VPN
traffic to metadata destinations.

The address plan is:

| Purpose | Address range |
| --- | --- |
| `yc-direct` clients | `10.242.10.0/24` |
| `aws-direct` clients | `10.242.20.0/24` |
| Future multi-hop clients | `10.242.30.0/24` |
| AWS IPv6 clients | deployment-generated ULA `/64` from a persistent `/48` |

Preflight validation rejects overlaps between VPN pools, VPC CIDRs, and each
other. Forwarding to private, link-local, metadata, multicast, and reserved
destinations is denied before general internet egress. IPv4 uses masquerading
on both nodes. AWS also uses NAT66 from its client ULA to the VM's public IPv6.

`yc-direct` pushes an IPv4 default route, tunnel DNS, and IPv6 blocking.
`aws-direct` pushes IPv4 and IPv6 defaults and tunnel DNS. Both modes push
OpenVPN's `block-local` redirect flag so that the client's directly connected
LAN is routed into the tunnel, except for the LAN gateway required to reach the
VPN endpoint. Unbound accepts DNS only from tunnel clients, performs recursion
over the selected exit node, and does not log queries.

An offline ECDSA root CA signs unique server and client certificates. Each
device and mode receives a distinct certificate and `tls-crypt-v2` key. Server
nodes never receive the CA private key or client private keys. Profiles and
Terraform state are local sensitive artifacts excluded from Git.

## Consequences

- The Direct MVP produces four profiles: Ubuntu and iPhone for each direct
  mode. Switching mode means selecting another profile.
- Yandex direct mode cannot provide IPv6 egress and must fail closed for IPv6.
- Host networking makes nftables and sysctl part of the deployment contract,
  but avoids ambiguous Docker bridge and NAT interaction.
- Multi-hop can later add separate OpenVPN instances and policy routing without
  changing the direct address pools or public ports.
- The old OpenVPN Access Server remains outside all state, inventory, playbooks,
  and rollback procedures.

## References

- [Yandex Cloud VM network interfaces](https://yandex.cloud/en/docs/compute/concepts/network)
- [AWS VPC IP addressing](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-ip-addressing.html)
- [OpenVPN 2.6 manual](https://openvpn.net/community-docs/community-articles/openvpn-2-6-manual.html)
- [OpenVPN Connect for iOS IPv6 support](https://openvpn.net/connect-docs/ios-faqs.html)
