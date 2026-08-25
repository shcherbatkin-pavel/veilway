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

## 4. Multi-Hop

Implement `yc-aws-multihop` only after both direct modes are stable. Completion
requires verified Yandex Cloud ingress, AWS internet egress, failure-safe
routing and DNS behavior, and continued isolation from both private cloud
networks.

## 5. Web UI

Implement the minimal single-user management interface and API, including
secure client-profile creation, delivery, rotation, and revocation. Bind the UI
to a non-public interface so it is reachable only through an operator-created
SSH tunnel. Completion requires authentication, input validation, audit-safe
logging, and confirmation that no management port or credential material is
publicly exposed.

## 6. Hardening and operations

Add encrypted remote state, backup and recovery, upgrades, monitoring,
security tests, threat-model review, and operator documentation. Completion
requires a tested rollback path and clean secret scan. Migrating or retiring
the old OpenVPN Access Server is a separate project requiring explicit scope
and approval.
