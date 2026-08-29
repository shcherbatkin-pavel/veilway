# ADR 0002: Rejected AWS Direct UDP/443 trial

- Status: Rejected after Direct MVP validation
- Date: 2026-08-25
- Outcome: ADR 0001 remains authoritative for Direct endpoint ports

## Context

The AWS OpenVPN control channel, certificate authentication, tunnel lease,
server forwarding, DNS, nftables, and NAT all passed isolated checks. A direct
client emitted valid OpenVPN UDP/1194 data-channel packets, but those packets
did not reach the EC2 host's pre-conntrack counter. The same protected client
profile and application image passed the complete data-channel and DNS test
when their outer transport traversed an already established VPN.

This localized the observed failure to the direct network path rather than the
Veilway server, profile identity, NetworkManager, or client implementation. A
bounded trial moved only `aws-direct` to UDP/443 to distinguish a port-specific
block from broader traffic classification.

## Trial

The AWS security group, OpenVPN listener, nftables ingress rule, inventory, and
two protected AWS profiles were moved from UDP/1194 to UDP/443. No VM, Elastic
IP, certificate, client private key, or `tls-crypt-v2` key was replaced. The
privileged listener temporarily required `NET_BIND_SERVICE` in the OpenVPN
container.

Server verification passed on UDP/443. Both the NetworkManager client and an
independent isolated OpenVPN client completed the control channel and received
a tunnel lease directly, but neither could pass data to the tunnel gateway.
The same isolated UDP/443 client then passed the data-channel and tunnel-only
DNS checks when its outer transport used an existing VPN.

## Decision

Reject UDP/443 as an AWS Direct workaround and restore the Direct baseline on
UDP/1194. Remove the trial-only `NET_BIND_SERVICE` capability. Do not try raw
OpenVPN TCP/443 merely because HTTPS commonly uses that port: raw OpenVPN does
not become HTTPS and the additional observation that the AWS website is
unreachable on the direct path further reduces the expected value of port-only
experiments.

Proceed to a separately reviewed Yandex-ingress, AWS-egress multi-hop design.
The client will reach the already accepted Yandex path, while the inter-cloud
transit bypasses the filtered client-to-AWS path.

## Consequences

- Terraform rollback must update only the existing AWS security group from
  UDP/443 to UDP/1194; VM replacement is forbidden.
- Ansible rollback is limited to `aws-direct` and restores its listener,
  firewall rule, Compose capabilities, and profiles without rotating PKI.
- `aws-direct` remains a valid independently deployed endpoint but is not
  accepted as reachable from the observed direct operator network.
- Multi-hop must be fail-closed: loss of AWS transit must never fall back to
  Yandex internet egress or Yandex DNS recursion.
- The old VPN service and its hosts remain outside all changes.
