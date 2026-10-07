# ADR 0005: Google identities, owned profiles and authoritative server PKI

- Status: accepted
- Date: 2026-10-05
- Supersedes ADR 0004's password login, three-container count and exclusion of
  registration/profile management/PKI; its restart allowlist and cloud permissions
  remain applicable.

## Context

The operator needs to issue VPN access to individual registered users, expose
their profile expiry and download, and revoke access without distributing CA
material across administration processes. The approved staged implementation
adds these functions to the existing public restart panel.

## Decision

`https://veilway.ru` hosts Caddy/React, FastAPI, PostgreSQL and an isolated PKI
service. Only Caddy publishes TCP/80+443. Caddy/API and API/PKI communicate over
separate Unix sockets. PKI has no network, Docker socket, cloud credentials or
access to PostgreSQL; only it mounts private CA/profile storage. Its encrypted
CA passphrase is a separate protected runtime input. The API receives private
profiles only through bounded authenticated download operations.

Google authorization-code OIDC uses only `openid email`. Registration is automatic
and gives USER without VPN access. The configured verified, Google-authoritative
operator email bootstraps one ADMIN and pins its Google `sub` in PostgreSQL;
later email/config changes cannot transfer that role. Password login is disabled.
Sessions remain server-side, with Secure/HttpOnly host cookies and session-bound
CSRF. USER visibility is filtered by owner in SQL, including jobs and downloads.
Only ADMIN can create, assign, rename or revoke profiles and manage the two nodes.

The original stages 1–8 managed only new profiles. The 2026-10-06 scope extension
adds [explicit operator import of existing profiles](../legacy-profile-migration.md)
without reissuing or changing existing client files. Profiles use immutable UUID identities, unique
keys per device/mode, explicit expiry (default 365 days, bounded by CA lifetime),
durable idempotency keys and immutable assigned owners. Imported registry history
preserves legacy certificates/revocations without adopting legacy users or
private profiles automatically. Separate offline import validates original
materials; metadata synchronization creates unassigned records and ADMIN
explicitly assigns registered USER owners. Downloads may repeat while active
and are never cached.

The existing dedicated Veilway CA is imported once; no replacement CA is generated.
After the approved handover, the server store is the single CA writer. Full
registry/newcerts, serial/CRL counters and revocations are preserved. Local CLI
writers serialize using a shared flock and are disabled by a persistent private
`.server-managed` marker, set under that same lock. This marker must accompany
all operator copies; it has no automatic undo. Server/legacy certificate
maintenance needs a separately reviewed authoritative procedure, never resuming
the stale local CA.

CRLs are signed full publications with monotonic versions and preserved revoked
sets. Dedicated outgoing agents atomically install them into directory mounts.
`revoking` blocks downloads immediately; `revoked` waits for the required node's
valid durable receipt. Agents never restart VPN servers or forcibly end existing
sessions. General VPN deployment skips local CRL copying in managed mode and
rejects legacy mode after local handover or remote agent bootstrap, protecting
against an old inventory/workstation overwriting revocations.

The panel deployment quiesces existing API/web/PKI containers before replacing
code and applying migrations; PostgreSQL persists. An operator-approved coherent
backup must precede deployment. Backup/recovery pairs PostgreSQL with the full
PKI store and node/version evidence. Restoring an old database or CA independently,
lowering counters, discarding receipts or deleting revocations is prohibited.
Unprovable post-backup changes keep writers stopped for operator reconciliation.

## Consequences

The panel VM's protected disk and encrypted backups now contain private CA and
client material. This deliberately replaces the earlier workstation-only CA
rule; VPN nodes still never receive the CA private key. UID/DAC separation,
encrypted CA storage, minimal RPC and explicit maintenance reduce exposure but
do not remove the panel host trust boundary. The operator must keep runtime
passphrases separate from backups and prevent simultaneous authorities.

There is no HA, MFA, billing, general cloud discovery or public role mutation
API. ADMIN account replacement is not implemented as a self-service flow.
CA/server-certificate renewal and revocation of imported legacy identities are
not public profile API operations. The system remains limited to the two
dedicated VPN nodes; existing OpenVPN Access Server is untouched.

Development completion does not authorize Google provisioning, CA export/import,
host deployment, CRL mount changes, timer activation or real VPN connections.
See [rollout and recovery](../profile-rollout.md) and
[security/live acceptance](../profile-security-acceptance.md).
