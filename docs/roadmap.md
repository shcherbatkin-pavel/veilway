# Veilway development roadmap

Later phases are intentionally gated by the findings and design decisions from
earlier phases. Completing a phase does not authorize changes to production or
prototype infrastructure.

## 1. Audit and requirements

Document the prototype, contributor safety rules, and deployment constraints.
The read-only diagnostics of the old Yandex Cloud and AWS Ubuntu VMs are
complete; their sensitive reports remain private and outside Git. Their
sanitized conclusions inform isolation, forwarding, and firewall ownership but
do not constrain the new dedicated VMs. This phase is complete.

## 2. Greenfield architecture ADR

Define OpenVPN 2.6 containers on new dedicated VMs, independent Terraform
stacks, Ansible deployment, host networking, nftables ownership, DNS, address
pools, PKI boundaries, and IPv4/IPv6 behavior. The checked-in ADR completes the
design; review remains required before applying infrastructure.

## 3. Direct VPN MVP

Implement and validate Terraform, Ansible, Docker Compose, `yc-direct`, and
`aws-direct` on explicitly approved new infrastructure. Completion requires
IPv4 egress through both providers, IPv6 egress through AWS, IPv6 leak blocking
through Yandex, tunnel-only DNS, private-network isolation, working profile
revocation, no credential material in Git or logs, and no contact with old VMs.

`yc-direct` passed acceptance. `aws-direct` passed server, nested-transport,
PKI, DNS, and forwarding checks, but its data channel is filtered on the
operator's direct network path on both UDP/1194 and UDP/443. ADR 0002 records
the rejected port trial. This path-specific result prevents full Direct
acceptance but does not require changing or deleting the dedicated AWS node.

## 4. Multi-Hop

ADR 0003 is accepted and `yc-aws-multihop` is implemented. The original
requirement for client-path acceptance of both Direct modes is waived only for
the documented AWS path-filtering condition: the same AWS endpoint passes when
reached through an existing tunnel. Automated Ubuntu acceptance passed IPv4,
IPv6, DNS, path MTU, private/metadata isolation, and cleanup through Yandex
ingress and AWS egress. Manual iPhone acceptance passed IPv4 and IPv6 on both
Wi-Fi and mobile networks, including recovery after disconnect. Both deployed
roles are idempotent and their read-only verifiers pass.

## 5. Public restart control plane

The implemented first web phase is a public, authenticated single-operator
restart dashboard for only `aws-direct` and `yc-direct`, described in ADR 0004
and the control-plane guide. It provides outbound heartbeats and sequential
restart jobs; a combined job waits for AWS recovery before restarting Yandex.
Authentication, CSRF protection, the two-node allowlist, audit-safe responses,
and handling ambiguous mutations are covered by local tests. Live acceptance
and each real restart remain separate, explicit operator actions.

Application users, client-profile delivery, rotation, revocation and PKI are
outside this web phase. Any future web management of profiles requires its own
design and security review; the existing local operator tools remain the
supported interface.

## 6. Hardening and operations

Add encrypted remote state, backup and recovery, upgrades, monitoring,
security tests, threat-model review, and operator documentation. Completion
requires a tested rollback path and clean secret scan. Migrating or retiring
the old OpenVPN Access Server is a separate project requiring explicit scope
and approval.
