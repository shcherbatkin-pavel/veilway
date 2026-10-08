# Signed CRL delivery (stage 5)

This stage is developed and locally verified. It has not been deployed to real
nodes. The existing OpenVPN Access Server is outside this deployment. Browser
views of delivery errors and the “Отзыв применяется” label are implemented in
[stage 6](profile-panel.md); the authenticated API and `revoking` state remain
the source of truth.

## Publication and completion

The isolated PKI socket now supports `{"operation":"crl"}`. It exports only the
full PEM CRL and public CA certificate, with bounded framing; no CA key, password,
server key, client key or profile is included. The PKI serializes export with
issue/revoke using its existing filesystem lock. It refreshes the signed CRL
when its last update is at least 24 hours old or less than 24 hours remain before
expiry. Each local revoke also generates a CRL. New CRLs last seven days. Refresh
uses the same fsynced generation transaction and monotonically increasing CRL
counter as revocation; old revoked serials remain in the full list.

The API's CRL worker polls PKI at the existing worker interval (three seconds by
default). A PostgreSQL singleton row lock serializes publication across API
processes. Before storing public CRL bytes, it checks CA validity, CA/CRL signing
usage, issuer, signature, CRL validity and a positive 64-bit CRL number. Delta and
indirect CRLs are rejected. The first published public CA fingerprint is pinned;
subsequent CA changes, lower versions, changed bytes at the same version and
lists omitting previously revoked serials fail publication. Errors are fixed
codes rather than exception text or material. The last valid published version
can still be delivered during a publisher outage; an expired version returns
`503` and never completes new revocations.

A local revoke job records its CRL number and succeeds independently of delivery.
The profile remains `revoking` and downloads stop immediately. Only a receipt
for a known, unexpired publication with its exact SHA-256 can unlock completion.
The worker verifies that the receipt version is at least the revoke version and
contains that profile's certificate serial. AWS Direct requires `aws-direct`;
Yandex Direct and Yandex/AWS Multi-hop require `yc-direct`. Each node receives the
same complete common-CA CRL, including historical revocations. A receipt from the
other node cannot complete the profile. Completion sets `revoked` and appends
`revoke_result/succeeded` to the existing audit journal.

Pending revocations survive API restarts and unavailable nodes. No timeout changes
them to successful. An acknowledgement proves the file installation at that
moment; it does not prove continuous node availability or recall already active
VPN sessions. CRL enforcement blocks new TLS connections, including reconnects.
No management-socket session termination is performed.

## Authentication and API

All paths below use `/api/v1`. Agent endpoints require a separate per-node
`Authorization: Bearer …` token. Browser sessions and heartbeat credentials are
not accepted as agent authentication. The provisioning CLI reads exactly two
raw tokens from protected stdin and stores SHA-256 digests only:

```text
veilway-control sync-crl-agents
stdin JSON: {"aws-direct":"<separate protected token>","yc-direct":"<separate protected token>"}
```

Tokens must be distinct, contain 32–256 printable non-whitespace characters and
differ from both heartbeat credentials. Rotating them invalidates old agent
authentication; already installed CRL receipts remain durable. Never paste real
tokens into commands, public docs, issue descriptions or logs.

| Method/path | Purpose |
| --- | --- |
| `GET /crl-agents/{slug}/bundle` | Latest valid full CRL: `version`, `sha256`, `crl_base64` |
| `POST /crl-agents/{slug}/receipt` | Exact installed `version` and `sha256`; `204`, idempotent |
| `POST /crl-agents/{slug}/error` | Fixed code: `transport_unavailable`, `invalid_bundle`, `installation_failed` |
| `GET /crl-delivery` | ADMIN session only; publisher error, latest version/expiry and both node states |

Responses use `no-store`; agent responses also use `no-referrer`. Wrong node or
credential returns `401`; an unknown, expired, incorrect or lower receipt returns
`409`. Administrator delivery status distinguishes `unconfigured`, `pending`,
`current`, `offline` (no contact for two minutes), `expired` and `error`. USER
cannot read this endpoint. There is no unauthenticated CRL or CA download.

## Restricted node installer

`deploy/roles/veilway_crl_agent/files/veilway-crl-agent` is a one-shot Python agent
using Ubuntu's `python3-cryptography` and an outbound HTTPS connection to the fixed
`https://veilway.ru` origin. It validates the TLS hostname and certificate, ignores
proxy environment variables, rejects redirects, bounds responses and accepts
only the fixed protocol fields. The server cannot choose destinations, filenames,
shell commands or CA trust anchors. The timer polls fifteen seconds after startup
and fifteen seconds after each invocation; errors retry on subsequent polls.

The root-owned configuration consists of a fixed node slug, token file and the
existing common public CA copied once into `/etc/veilway-crl-agent`. The account
is UID/GID 10004, without the VPN key-reading group. It has no inbound listener,
Docker socket, capabilities or access to `/etc/veilway/pki`; systemd makes the
filesystem read-only except for `/var/lib/veilway-crl`. Runtime code writes only
`crl.pem` and temporary candidates in that directory, never service configuration.

The directory is owned by `veilway-crl:veilway`, mode `2750` (group 900); the
setgid bit makes replacement files retain OpenVPN's reader group. CRL files are
`0640`, so the separate OpenVPN UID/GID 900 can read them without granting the
agent access to server keys. With managed CRLs, Direct, Yandex ingress and AWS
transit server containers also receive supplementary group 900 at startup.
OpenVPN initially reads the CRL as root before dropping privileges; the
capability-restricted root process otherwise cannot traverse the agent-owned
directory. This adds no capabilities and does not apply to the Yandex transit
client or DNS containers. The agent locks the directory inode, validates the
existing signed CRL as its durable anti-rollback state, writes a bounded candidate,
fsyncs it, renames atomically and fsyncs the directory. Missing, symlinked or
invalid installed files fail rather than bootstrap from a remotely supplied file.
An expired installed CRL may be replaced by a newer valid one. Older versions,
same-version changed bytes and missing revoked serials are rejected. It rereads
and validates the installed file before sending the receipt. A lost receipt causes
the next poll to acknowledge the same installed version without replacing it.

## Operator cutover — separate exact approval required

Follow the ordered [CA handover, panel rollout and recovery](profile-rollout.md)
first. General node deployment now refuses legacy CRL mode after a local handover
marker or remote agent bootstrap, and skips local CRL copying in managed mode.
Preserve `veilway_crl_agent_managed: true` in every applicable private inventory.

The following is a prepared procedure, not authorization to execute it. Obtain
approval for the concrete target and each host/service/configuration change.
Never target the existing Access Server. Keep local credentials in Git-ignored
`.env` with mode `0600`, and target only the two dedicated inventory hosts.

1. Add two independent random `CRL_AWS_DIRECT_TOKEN` / `CRL_YC_DIRECT_TOKEN` values
   to protected operator configuration. An approved web deployment migrates to
   `0005_crl_delivery` and synchronizes their hashes from protected stdin. Agent
   credentials are not mounted into the long-running API or PKI containers.
2. Approve the CRL bootstrap on each dedicated node. Run the operator wrapper
   for component `crl`, initially without `--enable-crl-agent`. This installs the
   Python dependency, isolated account, executable, protected CA/token/config,
   systemd files and initial CRL directory. It does not start a fresh timer. The
   bootstrap copies the existing common CA and CRL from `/etc/veilway/pki` without
   overwriting any installed versions. Validate the initial CRL against the same
   CA and ensure it is current before a VPN mount cutover.
3. Separately approve updating the dedicated OpenVPN configuration and recreating
   the affected dedicated VPN containers. Set `veilway_crl_agent_managed: true`
   in the private inventory. Rendered server configs use
   `crl-verify /etc/veilway/crl/crl.pem`; Compose mounts the **directory**
   `/var/lib/veilway-crl:/etc/veilway/crl:ro`. This covers Direct, Yandex ingress and
   AWS transit server configurations. Mounting the individual `crl.pem` file
   would retain its old inode after rename. Do not run the whole node playbook
   casually: it also contains separately protected network/service operations.
4. Confirm the directory mount and effective `crl-verify` path for every relevant
   running dedicated server. Then approve timer activation and invoke the wrapper
   with `--enable-crl-agent`. The role requires the managed inventory flag and
   mount acknowledgement and inspects only the fixed dedicated container mounts;
   it refuses activation unless every server has the read-only directory bind.
   It never restarts OpenVPN, changes routes or touches Access Server.
5. Check authenticated `/crl-delivery`, publisher state and both exact installed
   version/hash receipts. Perform a separately approved real-node acceptance test
   with temporary profiles: reconnect fails after revocation; another profile
   still connects; unavailable-node revocations remain pending. Record results
   privately and remove temporary VPN material according to operator policy.

Validation-only wrapper commands contact no host:

```bash
scripts/deploy-web-control.py crl --limit aws-direct
scripts/deploy-web-control.py crl --limit yc-direct --enable-crl-agent
```

`--apply` invokes the selected Ansible playbook and must only be added after exact
approval. There is no automatic production cutover in this development stage.
Do not remove or roll back the installed CRL. OpenVPN checks CRLs for new peers,
but a **missing CRL file allows connections with a warning**; therefore bootstrap
must precede changing the mount, and installation always preserves the existing
file on errors. See the [OpenVPN 2.6 manual](https://build.openvpn.net/man/openvpn-2.6/openvpn.8.html).
Restore procedures must preserve maximum published/acknowledged versions, common
CA fingerprint and the complete revoked set. Migration downgrade refuses to
discard nonempty publications or agent records. A CA change or corrupt local
state requires an operator-preserving repair rather than automatic rollback.

## Local verification

```bash
scripts/check.sh
scripts/test-profile-api.sh --build
scripts/test-crl-mount.sh --build
```

The backend suite uses disposable PostgreSQL when explicitly supplied through
`VEILWAY_TEST_POSTGRES_URL`; otherwise real PostgreSQL migration/concurrency
checks are visibly skipped. Integration images contain only synthetic CA fixtures.
The true OpenVPN 2.6 test uses loopback and `dev null`, so it needs no TUN device,
network capabilities or host route changes. It accepts a profile, installs its
revocation through the agent, rejects its reconnect and accepts another profile
with the same server PID. The mount script uses two binds of one disposable
public-CRL directory (writer RW, VPN reader RO) to test rename visibility directly.
This is a TLS/CRL acceptance check, not a packet-routing or live-node test.

CA/key/profile fixtures and transport logs remain temporary and protected; no
production material is read, generated artifacts committed or external audit
service used. CRLs are limited to 64 KiB and publications retained for version
history; exceeding that bound requires a separately reviewed protocol change,
not truncated delivery. Other tests cover signature/expiry rejection, monotonic
versions and revoked sets, daily renewal, both target rules, wrong/heartbeat
credentials, lost acknowledgements, missing/symlink files, durable receipts,
operator token synchronization and migration downgrade protection.

Implementation references: [cryptography X.509](https://cryptography.io/en/latest/x509/reference/)
and [OpenSSL ca](https://docs.openssl.org/3.0/man1/openssl-ca/).
