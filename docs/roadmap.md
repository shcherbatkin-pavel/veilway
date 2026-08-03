# Veilway development roadmap

Later phases are intentionally gated by the findings and design decisions from
earlier phases. Completing a phase does not authorize changes to production or
prototype infrastructure.

## 1. Repository foundation and VM audit

Document the prototype, establish repository safety rules, and collect
read-only diagnostics from both Ubuntu VMs. This phase is complete when the
audit script passes static checks, its output stays outside Git, and reviewed
reports from both hosts are available privately for architecture work.

## 2. Architecture and coexistence design

Compare the sanitized audit findings, select the VPN protocol and address
plan, define routing and DNS behavior, and document how Veilway will coexist
with OpenVPN Access Server. Completion requires a reviewed design with no
unresolved route, port, firewall, or address-pool conflicts.

## 3. Direct modes

Implement and validate `yc-direct` and `aws-direct` in an isolated or explicitly
approved environment. Completion requires working internet egress from both
client types, no access to provider-private networks, and no regression to the
existing OpenVPN service.

## 4. Client profile lifecycle

Define secure creation, delivery, rotation, revocation, and local storage of
Ubuntu and iPhone client profiles. Completion requires successful onboarding
and revocation tests without placing credential material in Git or logs.

## 5. Private web panel

Implement the minimal single-user management interface and API. Bind it so it
is reachable only through an operator-created SSH tunnel. Completion requires
authentication, input validation, audit-safe logging, and confirmation that no
management port is publicly exposed.

## 6. Multi-hop routing

Implement `yc-aws-multihop` after both direct modes are stable. Completion requires
verified Yandex Cloud ingress, AWS internet egress, failure-safe routing, DNS
behavior, and continued isolation from both private cloud networks.

## 7. Hardening and public release

Add reproducible deployment, backups and recovery, upgrades, monitoring,
security tests, threat-model review, and operator documentation. Completion
requires a clean secret scan, tested rollback, and a release checklist suitable
for a public MIT-licensed project.
