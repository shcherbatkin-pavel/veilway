# Veilway prototype requirements

## Purpose

Veilway will provide self-hosted, internet-only VPN connectivity through two
new, dedicated Ubuntu 24.04 LTS virtual machines: one in Yandex Cloud and one in
AWS. One ADMIN manages access for registered USER accounts on shared dedicated
nodes. There is no tenant infrastructure or billing.

## Users and clients

- One operator ADMIN, bootstrapped from protected Google email and pinned by
  Google subject; all other verified Google identities register as USER.
- Registration grants no VPN access. ADMIN assigns new profiles to USER;
  USER can list and download only their own active, unexpired profiles.
- The baseline has two client devices: an Ubuntu laptop and an iPhone.
- The operator may explicitly provision additional device profiles with
  non-personal identifiers.
- Client profiles and credentials must be generated and handled as secrets.

## Planned connection modes

- `yc-direct`: the client exits to the internet through the Yandex Cloud VM.
- `aws-direct`: the client exits to the internet through the AWS VM.
- `yc-aws-multihop`: the client enters through Yandex Cloud and exits through AWS.

All modes provide internet access only. They must not grant access to private
AWS or Yandex Cloud networks.

The first MVP implements `yc-direct`, `aws-direct`, and the separately accepted
`yc-aws-multihop` phase. `yc-direct` is IPv4-only and must block IPv6 rather
than let it bypass the tunnel. `aws-direct` and `yc-aws-multihop` provide IPv4
and IPv6 egress through AWS.

## Management and coexistence

- The accepted profile/restart panel is public at `https://veilway.ru` so it stays
  reachable during a VPN outage. It manages only `aws-direct` and `yc-direct`
  and uses Google registration, ADMIN/USER roles and server-side cookie sessions.
- ADMIN manages profile issue/ownership/rename/revocation and node restart/history.
  USER sees mode, expiry, status and download. All API responses are non-cacheable;
  mutations require session-bound CSRF. MFA and general VM administration are out
  of scope.
- Veilway deployment targets only newly created, dedicated VMs. Inventory and
  Terraform state must not reference the old VMs.
- The existing OpenVPN Access Server is outside the target architecture and
  must not be modified, stopped, restarted, removed, or disrupted.

## Deployment requirements

- Terraform creates isolated networks, security groups, static public IPv4
  addresses, and dedicated Ubuntu VMs in independently managed AWS and Yandex
  stacks.
- Ansible installs Docker and Docker Compose, manages host sysctl and nftables,
  and deploys the application after an explicit operator invocation.
- OpenVPN 2.6 and Unbound run in Compose-managed containers using host
  networking. OpenVPN is not installed as a host package or systemd service.
- The OpenVPN container receives `/dev/net/tun`, `NET_ADMIN`, the temporary
  `SETUID`/`SETGID` capabilities required to drop to its fixed unprivileged
  account, and `KILL` so PID 1 can forward stop signals after that UID change.
  It drops all other capabilities and uses a read-only root filesystem.
- The host nftables ruleset is the single owner of forwarding and NAT. Docker
  bridge networking and Docker-managed port publishing are not used.
- SSH ingress is limited to operator-provided CIDRs. UDP/1194 remains the
  Direct listener on both VMs. Yandex UDP/1195 is the multi-hop client ingress;
  AWS UDP/1196 accepts transit only from the Yandex static IPv4 `/32`.

## VPN and PKI requirements

- Each device and mode uses a unique client certificate and `tls-crypt-v2` key.
- TLS 1.3, AEAD data ciphers, certificate revocation, and disabled compression
  are mandatory. Password-only authentication is not supported.
- Before the approved handover, local PKI tooling prepares the dedicated CA.
  After handover, its sole writer is the network-isolated server PKI on the panel
  VM, with an encrypted CA key and protected private profile storage. VPN nodes,
  API, Caddy and PostgreSQL never mount private CA storage. This replaces the
  earlier workstation-only rule under [ADR 0005](adr/0005-google-profiles-and-server-pki.md).
- Preserve the full existing registry, counters and revoked set during import
  and recovery. Disable local PKI writes and stale deployment CRL copying.
- Issue new UUID profiles, default duration 365 days within CA lifetime,
  with durable idempotency and worker recovery. Assigned owners cannot transfer.
- Import existing profiles under the same CA without replacing certificates,
  keys, endpoints or expiry; explicitly assign owners, preserve original download
  bytes, revocations and provenance. Follow the [legacy migration procedure](legacy-profile-migration.md).
- Signed full CRLs reach dedicated outgoing agents. `revoking` disables download;
  `revoked` requires the relevant node's acknowledgement. Active sessions are not
  forcibly ended; subsequent connections enforce CRL and certificate expiry.
- Servers receive only their own key and certificate, the public CA,
  certificate revocation list, and endpoint-specific `tls-crypt-v2` server key.
- The Yandex transit client receives only its own client key and certificate,
  public CA, and AWS-transit-specific `tls-crypt-v2` client key.
- DNS is served locally by Unbound, is reachable only from the VPN tunnel, and
  must not log individual queries.

## Security requirements

- The repository is public; no private keys, certificates, passwords, tokens,
  client profiles, audit reports, or unredacted infrastructure exports may be
  committed.
- Infrastructure inspection is read-only unless a later task explicitly
  authorizes a narrowly scoped change.
- Audit tooling must not connect to hosts automatically or transmit collected
  data.
- Audit output must remain local, have restrictive filesystem permissions, and
  be excluded from Git.
- Privileged diagnostics must be optional, clearly disclosed, and read-only.

## First-iteration deliverables

- Reviewed architecture decision record and sanitized audit conclusions.
- Independent Terraform stacks for AWS and Yandex Cloud.
- Explicit Ansible deployment for the two new VMs.
- Compose-managed OpenVPN and Unbound services for both direct modes.
- Local PKI tooling for bootstrap, disabled after server handover.
- Google registration and ADMIN/USER profile panel with isolated server PKI.
- Fail-closed Yandex-to-AWS multi-hop routing and transit-only DNS.
- Static validation and operator acceptance-test instructions.
- A separately deployable four-container profile/restart control plane, outbound
  heartbeat and CRL agents for the two dedicated VPN VMs, and coherent protected
  backup/recovery and approved live-acceptance procedures.

## First-iteration non-goals

- MFA, tenant provisioning, billing, cloud discovery, public ADMIN-role mutation,
  or management of any VM other than `aws-direct` and `yc-direct`.
- Migrating or modifying the old VMs or existing OpenVPN Access Server.
- Automatically applying Terraform or connecting to any VM.
