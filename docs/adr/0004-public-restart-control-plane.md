# ADR 0004: Public restart-only control plane

- Status: accepted
- Date: 2026-08-31

Authentication, container count and PKI/profile scope are superseded by
[ADR 0005](0005-google-profiles-and-server-pki.md). The decision below records
the original restart-only iteration; restart permissions and coexistence apply.

## Context

The earlier prototype requirement postponed the web panel and required a
private listener reached through an SSH tunnel. That makes recovery dependent
on operator network access precisely when the VPN may be unavailable. The
first useful management action is restarting either of the two dedicated VPN
VMs, or restarting both in a safe order.

## Decision

The canonical control-plane address is `https://veilway.ru`. A new Yandex
Cloud VM hosts exactly three steady-state containers: Caddy plus a React SPA,
FastAPI, and PostgreSQL. Only TCP/80 and TCP/443 are published. Caddy reaches
FastAPI through a Unix socket; PostgreSQL is reachable only on an internal
Compose network.

The MVP has one administrator synchronized from the operator's ignored local
`.env`. The plaintext password enters the bootstrap command through stdin and
only an Argon2id hash is stored. Runtime database and AWS secrets are installed
as root-owned `0400` files. The API entrypoint copies them to sealed anonymous
memory, permanently drops to UID/GID 10001, and exposes only inherited file
descriptors to the application. Browser sessions are server-side and use a
host-only `Secure`, `HttpOnly`, `SameSite=Strict` cookie plus CSRF tokens.

The database allowlist and API contain exactly `aws-direct` and `yc-direct`.
The AWS IAM user may reboot only the exact AWS instance ARN. Its access key is
created outside Terraform. The web VM service account gets `compute.operator`
only through an instance-level binding on `yc-direct`; it receives no folder
role. The web VM, legacy OpenVPN Access Server and every other VM are outside
the target set.

Yandex restart completion is read through the instance-specific Compute API
operations list. The global operation endpoint is not used because it is not
authorized by the deliberately instance-scoped binding.

Restart jobs are durable PostgreSQL records. A multi-target job always runs
AWS before Yandex and advances only after a later authenticated heartbeat has
a different boot ID and all locally allowlisted containers are healthy. The
worker persists `dispatching` before the one cloud mutation. A process failure
while the mutation result is ambiguous becomes `needs_review`; a persisted
`waiting` target resumes observation without sending another reboot.

Registration, MFA, PKI, profile generation, cloud discovery, SSH-based VM
commands and general VM administration are not part of this control plane.

## Consequences

The panel remains reachable during a VPN outage, at the cost of maintaining a
small public authentication surface. TLS termination, strict cookies, CSRF,
least-privilege cloud identities, a two-item database constraint and hidden
cloud identifiers reduce that surface. There is no HA: the design accepts one
web VM and one worker for this MVP.

The web VM accepts SSH connections from any IPv4 address so emergency access
does not depend on a fixed operator network. SSH remains key-authenticated;
the panel's login and password authenticate only the HTTPS application.

Terraform apply, access-key creation, DNS changes, Ansible deployment,
heartbeat installation and every real restart remain separate operator-
approved operations. The heartbeat role installs an outbound-only timer and
does not edit, reload or restart OpenVPN.

The public `veilway.ru.` zone and its apex A record are managed alongside the
web infrastructure in Yandex Cloud DNS. REG.RU remains the registrar and
delegates the domain to the authoritative Yandex Cloud name servers only after
Terraform has created the complete public zone.
