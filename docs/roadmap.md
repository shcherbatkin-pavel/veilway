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

The original restart-only phase is superseded for identities/profiles/PKI by
[ADR 0005](adr/0005-google-profiles-and-server-pki.md). Its two-node restart
allowlist and cloud-permission boundaries remain in force.

The agreed next web iteration is tracked in the
[Google sign-in and profile management implementation plan](profile-management-plan.md).
Its eight development stages have been executed individually; their status
and validation results are recorded in that document. Use the
[rollout, backup and recovery runbook](profile-rollout.md) after separate exact
approval. After handover the server PKI is the sole CA writer; local tools are
disabled and general node deployment cannot overwrite managed CRLs.

The 2026-10-06 scope update preserves the existing CA and working legacy
client profiles during the panel upgrade. Explicit offline profile import and
metadata synchronization into USER accounts are implemented; CA import alone
does not adopt private profiles or assign owners.
See the [migration and CA lifecycle plan](pki-evolution-plan.md) for individually
tracked stages M1–M4. ADMIN-driven CA registration, node rollout and retirement
are future stages C1–C4, with continued old-client compatibility as a release gate.

## 6. Hardening and operations

Add encrypted remote state, backup and recovery, upgrades, monitoring,
security tests, threat-model review, and operator documentation. Completion
requires a tested rollback path and clean secret scan. Migrating or retiring
the old OpenVPN Access Server is a separate project requiring explicit scope
and approval.
