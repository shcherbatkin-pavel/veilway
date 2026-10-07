# Isolated PKI service (stage 3)

`web/pki/` implements the offline PKI behind a private Unix socket. The public
profile API and its PostgreSQL job worker are implemented in [stage 4](profile-api.md). [CRL delivery and node acknowledgements](crl-delivery.md) are implemented in stage 5. No production CA, VPN server, route, firewall,
or existing OpenVPN Access Server is changed by building or testing this service.

The complete [stage-8 rollout/backup/recovery procedure](profile-rollout.md) is
authoritative for the handover. Local writers must be disabled with the private
handover marker; general node deployment skips local CRLs in managed mode and
rejects legacy mode after handover/agent bootstrap.

## Boundaries

Compose now has four services. `pki` has `network_mode: none`, a read-only root,
no Docker socket, no TUN device, no published ports and no cloud credentials.
Its only persistent mount is `/var/lib/veilway-pki`, owned by UID/GID 10002,
mode `0700`. Every stored file, including certificates and CRLs, is `0600`.
API/Caddy/PostgreSQL do not mount this storage or any parent of it.

The API mounts only the socket directory, read-only. The socket is
`/run/veilway-pki/pki.sock`, owner 10002, group 10003, mode `0660`. The socket
directory is `0710` so the API can traverse a known pathname but cannot list,
create or replace sockets. This directory contains no secrets. The API retains
UID 10001 and receives supplementary group 10003. Caddy has neither the mount
nor that group. The PKI server additionally checks Linux `SO_PEERCRED` and
accepts only UID 10001, even if another user can bypass filesystem permissions.

Only root startup reads the separate runtime inputs `pki_ca_passphrase` and
`pki_endpoints`, owned by root, mode `0600`, outside the PKI storage. It seals
inherited anonymous descriptors, drops to UID/GID 10002 and group 10003, and
executes only `serve` or explicit `import-ca --source PATH`. No plaintext
password appears in a subprocess argument or Docker environment. Signing
passes it through a sealed descriptor to OpenSSL. The CA key must be encrypted
PKCS#8; an unencrypted key is refused. No decrypted CA key is written to disk.
Client keys and `.ovpn` are private plaintext files in the protected storage,
so the data disk and backups must also be protected.

## Private protocol and application metadata

The socket accepts one JSON request per connection, framed by a four-byte
unsigned big-endian byte length. Maximum request: 4096 bytes; response: 131072
bytes. Duplicate or extra fields, invalid UUIDs, arbitrary paths, unknown
operations and shell payloads are rejected. There are at most eight active
handlers, bounded socket reads and 30-second subprocess deadlines. Executables,
OpenSSL configuration, server identities and ports are fixed in the service.
Requests cannot select a binary, command, config, extension or CA subject.

- `issue`: `profile_id`, `job_id` (the application's stable idempotency UUID),
  `mode`, `expires_at` (UTC `YYYY-MM-DDTHH:MM:SSZ`). Returns identifiers, serial,
  expiry, mode and certificate SHA-256, without keys or profile contents.
- `download`: `profile_id`. Returns that new profile as base64. Expired or
  locally revoked certificates cannot be downloaded. There is no profile list,
  legacy-profile import or server-certificate endpoint.
- `revoke`: `profile_id`, `job_id`. Returns the CRL number after local revocation.
  This is not confirmation from a VPN node; delivery is handled by stage 5.
- `crl`: no extra fields. Exports bounded base64 full CRL and public CA certificate
  for the CRL worker, refreshing the signed generation daily. It exports no key.

`web/backend/src/veilway_control/pki.py` provides the typed, bounded socket
client. User identity, ownership, device labels, profile expiry/state and job
state belong to PostgreSQL's existing `VpnProfile`/`ProfileJob` models. The
PKI stores only private material, the OpenSSL registry/counters and technical
receipts needed to replay CA operations. It has no user database or DB credentials.
The stage-4 profile API/job worker now uses this client.

The certificate CN is the immutable profile UUID. Display names cannot alter
it. Profiles match the existing dedicated endpoints:

| Mode | Server identity | UDP port | Extra client directive |
| --- | --- | --- | --- |
| `yc-direct` | `yc-direct` | 1194 | `block-ipv6` |
| `aws-direct` | `aws-direct` | 1194 | — |
| `yc-aws-multihop` | `yc-multihop-ingress` | 1195 | — |

Profiles include distinct EC P-256 client keys, `clientAuth`, CA/server
verification, TLS >= 1.3, AEAD ciphers, no compression and endpoint-specific
OpenVPN-generated `tls-crypt-v2` client keys. Expiry cannot exceed CA expiry.

## Commit, retry and recovery

Every access uses a cross-process `flock` on the protected store. A mutation
copies the committed generation into a private candidate, changes its CA
registry and material, writes its receipt, fsyncs all files/directories, then
atomically replaces and fsyncs `CURRENT`. Only after that commit can a response
leave the service. Files have no hard links between generations.

An interrupted pre-commit operation has no published certificate/CRL. A retry
removes the unpublished generation and uses the committed counters. If a commit
succeeded but the response was lost, the same job UUID and request return the
saved result, without issuing again or incrementing the CRL. Reusing a job UUID
with another operation/payload, or issuing an existing profile under a new job,
returns `conflict`. Repeated local revocation never changes the CRL again.

Startup cleans only unpublished service-owned generations and refuses malformed
storage, symlinks, hard-linked files or incorrect permissions. A separate server
lock prevents a second process from replacing the live socket. SIGTERM/INT stop
accepting connections, finish in-flight handlers and remove the socket. Abrupt
termination is covered by the generation recovery path.

The current implementation copies the whole generation per mutation and deletes
old generations after committing. Provision at least space for two complete
generations plus temporary material. Exhaustion before commit leaves the prior
state authoritative; retry after freeing space. This is a deliberately simple,
serialized store suitable for the initial panel, not a high-throughput CA.

## Manual import, only after approval

These are operator instructions, not automatic deployment actions. Obtain exact
approval before reading/copying real PKI, transferring it to the panel VM,
starting/stopping a service or replacing a deployment. Do not connect to hosts
or run the following production commands as part of a coding/test task.

1. Freeze all writers of the existing *dedicated Veilway* CA. Do not touch
   OpenVPN Access Server. Reconcile the latest CRL and revocations before export.
   Keep that freeze through the handover: two writers can reuse serial numbers
   or erase revocations. No server creates a replacement CA automatically.
2. Make a protected, coherent export containing exactly the inputs below. Copy
   the full `newcerts` history, including revoked and expired certificates.
   Do not include old `.ovpn`, client private keys or server private keys.

   ```text
   approved-pki-import/
     ca/ca.crt
     ca/private/ca.key             # encrypted PKCS#8
     ca/index.txt                 # complete OpenSSL registry
     ca/serial                    # next unused hexadecimal serial
     ca/crlnumber                 # next unused hexadecimal CRL number
     ca/newcerts/<serial>.pem      # every certificate recorded in index.txt
     crl.pem                      # latest signed, currently valid CRL
     endpoints/yc-direct/tls-crypt-v2-server.key
     endpoints/aws-direct/tls-crypt-v2-server.key
     endpoints/yc-multihop-ingress/tls-crypt-v2-server.key
   ```

3. Put the actual password in the separate protected `PKI_CA_PASSPHRASE`
   operator input; configure plain IPv4 `PKI_YC_ENDPOINT` and `PKI_AWS_ENDPOINT`.
   The inherited CA password must be nonempty; it is accepted even when shorter
   than 20 characters. Import verifies it by decrypting the existing encrypted
   key. This does not rotate the password or allow unencrypted CA keys.
   The deployment writes a PKI-only endpoint JSON mapping; multi-hop uses the
   Yandex address. Neither actual addresses nor password belong in Git. Endpoint
   selection is frozen into the imported generation, so later runtime config
   changes do not silently rewrite already issued profiles.
4. Build the image and prepare the dedicated protected store/socket directories
   through the separately approved deployment. The import bundle must be
   readable as container UID 10002: directory `0700`, files `0600`, matching
   numeric ownership. The runtime socket directory must be recreated on reboot
   (`/etc/tmpfiles.d/veilway-pki.conf` is prepared by the role). PKI fails closed
   until import is committed; API restart controls can run independently.
5. With PKI stopped and the bundle staged outside public build/Git paths, perform
   the approved one-time import. For default deployment paths:

   ```bash
   docker compose --file /opt/veilway-control/app/compose.yaml run --rm --no-deps \
     --volume /private/approved-pki-import:/import:ro \
     pki import-ca --source /import
   ```

   Import verifies CA key/certificate matching, encryption, signature/CA usage,
   expiry, historical certificate signatures/serials, registry consistency,
   monotonic serial/CRL counters, CRL signature/validity and equality of its
   revocations to the registry, plus all three `tls-crypt-v2` server keys.
   Missing history, stale CRLs and unexpected links/counters fail closed. It
   does not run copied OpenSSL configs. Existing committed stores cannot be
   overwritten or reimported. Rejected imports leave no authoritative `CURRENT`;
   a retry cleans unpublished candidate material.
6. Start PKI under a separate approval, check only its fixed readiness/error
   message, remove the staging bundle through a protected operator procedure,
   and take a consistent backup. Old registry entries remain for CA continuity
   and CRL integrity. Adopt existing private profiles only through the separate
   [legacy import and metadata synchronization](legacy-profile-migration.md).
   Stages 4–5 are implemented; this import still does not prove production
   issuance or node revocation cutover. Do not allow the old deployment to overwrite the
   service's CRL or reopen an independent CA writer.

## Backup and recovery

Back up PostgreSQL and the complete PKI store coherently, with PKI mutations
quiesced (a separately approved maintenance stop). Keep encrypted CA and client
material in a protected/encrypted backup; keep its runtime passphrase separately.
Copy `CURRENT`, all referenced generation files, registry, counters, receipts
and profile material. A CA key alone is insufficient for recovery. Lock files
are not authoritative, but must remain private if copied.

Never restore an older registry/CRL over a newer one or decrement counters.
Reconcile all post-backup issues and revocations before resuming. If that cannot
be proven, remain stopped and recover operator-side; there is no automatic
rollback to an older generation or replacement CA. Stage 4 retries
uncertain PostgreSQL jobs with their original idempotency UUID rather than allocating new
jobs after a timeout. [Stage 5](crl-delivery.md) adds daily serialized CRL refresh, bounded public CRL/CA
export and node delivery. New local revocations also generate CRLs.

## Local verification

```bash
scripts/test-pki-service.sh --build
scripts/check.sh
```

The PKI smoke builds the production image and a separate test image. It uses
only randomly generated synthetic CA material in disposable tmpfs, no network,
read-only root and no Docker socket inside the container. The root test
orchestrator has `CHOWN` solely to provision distinct UID fixtures; the production
PKI manifest has only `SETUID`/`SETGID`, which startup drops when switching users.
Tests exercise real OpenSSL/OpenVPN, all modes, imports, concurrent requests,
real process crashes before/after commit, CRL enforcement, socket framing,
peer UID/DAC boundaries, log redaction and graceful shutdown. No production
material is read. Backend tests separately exercise the socket client's framing,
response validation and sanitized errors.

Protocol/crypto references: [OpenSSL ca](https://docs.openssl.org/3.0/man1/openssl-ca/),
[OpenVPN 2.6](https://build.openvpn.net/man/openvpn-2.6/openvpn.8.html), and
[Python Unix sockets](https://docs.python.org/3.12/library/socket.html).
