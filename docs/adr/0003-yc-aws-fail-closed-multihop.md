# ADR 0003: Fail-closed Yandex ingress and AWS egress multi-hop

- Status: Accepted
- Date: 2026-08-25
- Depends on: ADR 0001 and the rejected trial in ADR 0002

## Context

`yc-direct` passed client acceptance. The independently healthy `aws-direct`
endpoint passes its complete data channel when reached through another tunnel,
but direct OpenVPN data is filtered on the observed operator network on both
UDP/1194 and UDP/443. Raw TCP/443 would not reproduce HTTPS and is not selected
as another port-only experiment.

The existing dedicated Yandex and AWS VMs already reserve client pool
`10.242.30.0/24`, Yandex UDP/1195 for a future ingress, and AWS UDP/1196 for a
future transit. Multi-hop can bypass the filtered client-to-AWS path without
changing client applications or involving any old host.

## Decision

Add an independent `yc-aws-multihop` mode on the two existing dedicated VMs:

```text
Ubuntu or iPhone
  -> OpenVPN UDP/1195 on the Yandex static IPv4
  -> encrypted OpenVPN transit from Yandex to AWS UDP/1196
  -> AWS-filtered internet egress and AWS DNS recursion
```

Keep both Direct listeners on UDP/1194. The multi-hop client ingress is a
separate OpenVPN server on Yandex with its own tunnel interface, server
identity, `tls-crypt-v2` key, client pool, and profiles. Yandex initiates a
separate OpenVPN client session to an AWS transit server. The AWS security
group permits UDP/1196 only from the Yandex node's static public IPv4 `/32`;
Yandex UDP/1195 is the only new public client ingress.

Use `10.242.40.0/29` only for the inter-node transit. AWS assigns a stable
transit address to the single Yandex transit identity and installs both the
kernel route and OpenVPN `iroute` for `10.242.30.0/24`. The persistent AWS ULA
`/48` supplies a separate `:30::/64` for multi-hop clients. IPv6 is carried
inside the IPv4 inter-cloud OpenVPN transport even though the Yandex VPC itself
is IPv4-only, then NAT66 egresses from AWS.

AWS Unbound also listens on the AWS transit tunnel address and accepts queries
from the multi-hop client pools. Multi-hop profiles receive that address as
their only DNS resolver. Yandex does not recurse or forward multi-hop DNS using
its own internet path.

All OpenVPN processes continue to use host networking in separate
Compose-managed containers. Each instance has a unique public port and tunnel
interface, so Docker publishes no ports and creates no bridge NAT. Ansible
continues to own one atomic host nftables ruleset per VM.

## Fail-closed routing contract

The Yandex node must never provide fallback egress for the multi-hop pools:

- its nftables forward chain permits multi-hop client sources only between the
  multi-hop ingress and transit interfaces;
- it contains no Yandex masquerade rule for the multi-hop IPv4 or IPv6 pools;
- dedicated IPv4 and IPv6 policy tables contain the transit default while the
  tunnel is usable and an explicit unreachable default beneath it;
- private, VPC, link-local, metadata, multicast, and reserved destinations are
  denied before transit forwarding;
- loss of the AWS transit therefore causes internet and DNS failure rather
  than lookup fall-through to the Yandex WAN route.

AWS applies the same destination denies before forwarding transit traffic to
its WAN interface, then owns IPv4 masquerade and IPv6 NAT66 for the multi-hop
pools. Return traffic is accepted only as established or related traffic or on
the explicit transit route.

## PKI and operator interface

The offline CA creates three new identities without reusing Direct private
material:

- `yc-multihop-ingress` server;
- `aws-transit` server;
- `yc-transit` client with the only transit `tls-crypt-v2` client key.

The operator lifecycle adds two device profiles:

```text
profile create --device ubuntu --mode yc-aws-multihop
profile create --device iphone --mode yc-aws-multihop
```

Those profiles use the Yandex static endpoint on UDP/1195 and unique client
certificates and `tls-crypt-v2` keys. Revocation continues to use the shared
offline CRL and never places CA or client private keys on either VM.

## Infrastructure and deployment sequencing

Terraform roots remain independent. The Yandex stack adds only the reviewed
UDP/1195 ingress rule. The AWS stack accepts an operator-supplied Yandex static
IPv4 `/32` and adds only the reviewed UDP/1196 transit rule. No stack reads the
other stack's state automatically, and neither VM receives a cloud IAM role.

Deployment is staged and explicitly approved:

1. validate both Terraform plans and their exact security-group changes;
2. deploy and verify the AWS transit listener and AWS routing/DNS policy;
3. deploy the Yandex stack; Compose waits for a healthy transit client before
   starting the multi-hop ingress, while nftables and policy routing remain
   fail-closed throughout;
4. verify the Yandex transit and multi-hop ingress;
5. create and import the two multi-hop profiles;
6. run acceptance with every unrelated VPN disabled.

Direct containers and profiles remain available throughout. Rollback removes
the multi-hop containers, routes, policy rules, PKI copies, and UDP/1195 and
UDP/1196 ingress rules without replacing either VM or changing Direct pools.

## Acceptance criteria

- Ubuntu and iPhone IPv4, IPv6, and DNS egress are observed through AWS.
- The Yandex public address is never observed as multi-hop client egress.
- Both cloud VPCs, metadata endpoints, private ranges, and link-local ranges
  remain unreachable from the client.
- Stopping or breaking transit in an explicitly approved failure test removes
  internet and DNS access for the multi-hop profile without Yandex fallback.
- `yc-direct` remains healthy while multi-hop is working and while transit is
  unavailable.
- Repeated deployment is idempotent, reboot restores the intended state, and
  no secrets appear in Terraform state, Git, Ansible output, or container logs.

## Consequences

- Multi-hop adds latency and makes both dedicated VMs availability dependencies.
- Yandex terminates the client tunnel before traffic is re-encrypted for AWS;
  this is a routing design, not an end-to-end opaque relay through Yandex.
- AWS Direct remains useful from networks that do not filter it, but it is not
  the selected operator path on the currently observed network.
- If the Yandex-to-AWS transit is also filtered, a camouflage protocol or an
  independently reachable relay requires a new ADR; more public port changes
  are not an acceptable fallback.
