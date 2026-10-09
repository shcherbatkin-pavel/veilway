# Google/profile panel rollout and recovery (stage 8)

For the merged refactoring release, use the separately reviewed
[API/web-only rollout](api-web-rollout.md). The full web role below also
quiesces PKI and runs migrations; it is not the point-update procedure.

This is the operator runbook for the completed development plan, not permission
to deploy. Every real Google/cloud/host operation, CA export/import, service
stop/start, VPN mount change and agent activation needs approval for the exact
target/action under `AGENTS.md`. Do not execute production commands during a
coding task. Only the new Veilway panel and dedicated AWS/Yandex nodes are in
scope; the existing OpenVPN Access Server stays running and unchanged.

## Release prerequisites

Use a reviewed coherent release of backend/frontend/PKI, migrations through
`0006_legacy_profiles`, agent code and Ansible roles. Publish/merge PRs only after
separate authorization and the repository's squash-merge checks. The staged
development worktree is not evidence of a published or deployed release.

Before any apply, record privately: source revision and built image digests,
intended dedicated hosts, data disk, CA fingerprint, highest used serial/CRL
number, complete revoked set, publication version/hash/expiry, both node receipts,
inventory/Compose paths and backup location. Obtain these only through the
approved operator export/backup procedure and authenticated ADMIN status; do not
discover existing secrets or dump host journals/service environments. Evidence
and backups contain sensitive infrastructure/user data and must remain private.

The panel VM and its persistent disk must already be separately provisioned,
with approved DNS/TLS and least-privilege restart permissions. The role may install
packages, configure storage/firewall, build images, migrate the DB and stop/start
panel containers. Approving deployment must cover these concrete actions; it
never grants approval to modify the existing Access Server or unrelated resources.

## Google and protected operator inputs

1. In the approved Google project create an OAuth client of type **Web
   application**. Configure branding, audience and production availability for
   the intended accounts. The exact authorized redirect URI is
   `https://veilway.ru/api/v1/auth/google/callback`, without a trailing slash.
   It must match the backend callback exactly. This application navigates to
   the server; it has no browser Google SDK or browser-side client secret.
   [Google's OIDC reference](https://developers.google.com/identity/openid-connect/reference).
2. The application requests only `openid email`, with no Gmail mailbox access
   or offline/refresh-token use. Do not use Google's Testing user list as the
   VPN authorization boundary: basic identity scopes have an exception to that
   allowlist. Registration remains USER with no profile access until assignment.
   [Google's app-state overview](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview).
3. Copy `.env.example` to ignored `.env`, replace every placeholder privately,
   and set `0600`. Do not `source` it, use shell history for secrets, paste real
   email/credentials into commands or store them in inventory/extra-vars files.
   The wrapper parses an allowlist without shell evaluation and rejects unknown
   keys, placeholders, bad permissions, endpoints and duplicate agent tokens.
4. `ADMIN_GOOGLE_EMAIL` is the actual operator's verified Gmail or
   Google-authoritative Workspace account. Matching is case-insensitive without
   Gmail dot/plus normalization. First matching login pins Google `sub` in the
   singleton binding. Editing the email later cannot transfer ADMIN; recovery
   must preserve that binding. There is no public admin-replacement API.
5. Store only protected inventory under ignored `deploy/control-inventory.yml`
   and the dedicated node inventory/host_vars; use `0600`. Use separate random
   32–256 printable-character credentials for AWS/Yandex heartbeats and CRL
   agents. Retain the current PostgreSQL password on upgrade: changing its file
   does not rotate the existing database role password.

| Operator input | Protected runtime destination / use |
| --- | --- |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ADMIN_GOOGLE_EMAIL` | Panel `secrets/google_client_id`, `google_client_secret`, `admin_google_email`, root `0400`; API sealed descriptors |
| `POSTGRES_PASSWORD` | Panel `secrets/postgres_password`, root `0400`; DB initialization and API connection |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Panel root `0400` files; API restart only for the approved exact AWS instance |
| `AWS_REGION`, `AWS_DIRECT_INSTANCE_ID`, `YC_REGION`, `YC_DIRECT_INSTANCE_ID` | Protected stdin to immutable two-node DB synchronization; no infrastructure values in browser responses |
| `HEARTBEAT_AWS_DIRECT_TOKEN`, `HEARTBEAT_YC_DIRECT_TOKEN` | Separate node agents; only hashes synchronized into DB |
| `CRL_AWS_DIRECT_TOKEN`, `CRL_YC_DIRECT_TOKEN` | Separate node CRL agents; only hashes synchronized into DB, not mounted into long-running API |
| `PKI_CA_PASSPHRASE` | Panel `secrets/pki_ca_passphrase`, root `0600`, PKI-only mount/sealed descriptor |
| `PKI_YC_ENDPOINT`, `PKI_AWS_ENDPOINT` | PKI-only `secrets/pki_endpoints`, root `0600`; plain IPv4, imported/frozen mapping, Multi-hop uses Yandex |

Default secret root is `/etc/veilway-control/secrets`; it is root `0700`.
The operator `.env` is consumed on the operator machine, not copied wholesale
to the host. Ansible uses `no_log` for secret writes/stdin. The deployed
`/opt/veilway-control/app/.env` contains **only runtime paths**, root `0600`:
data, API socket, PKI socket and secret roots. It contains no credential values.
Compose loads this project file for path interpolation; keep it with the manifest
and use the same settings for maintenance commands.
[Compose interpolation](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/).

Validation-only commands on the operator checkout contact no host:

```bash
scripts/deploy-web-control.py web
scripts/deploy-web-control.py heartbeat --limit aws-direct
scripts/deploy-web-control.py heartbeat --limit yc-direct
scripts/deploy-web-control.py crl --limit aws-direct
scripts/deploy-web-control.py crl --limit yc-direct
```

`--apply` is the explicit execution switch and must only be added after exact
approval; never add it during development validation. Public example inventories
contain documentation addresses only and must not be used for a real apply.

## Ordered handover and cutover

Scope update 2026-10-06: retain the existing dedicated Veilway CA and existing
client connectivity; do not replace server certificates or wrapping keys to
start fresh. CA history and legacy client materials have separate imports.
Use the [legacy profile migration procedure](legacy-profile-migration.md)
after importing the same CA to populate USER cabinets without reissuing.
Development status is tracked in the [migration plan](pki-evolution-plan.md). Future ADMIN CA
creation/registration, node rollout and retirement are outside this cutover.

1. **Freeze the old authority.** Approve an operator maintenance window and
   stop all writers/automation for the dedicated Veilway CA. Upgrade every
   operator checkout to the guarded release. In each approved checkout run:

   ```bash
   scripts/veilway-pki handover --confirm-server-managed
   ```

   The command holds `.writer.lock`, waits for guarded writers and creates a
   private `.server-managed` marker. It does not alter CA material. All local
   init/server/transit/profile changes then fail before touching CA inputs.
   Keep the marker on every clone/export of that operator CA. Old program
   versions and direct OpenSSL invocations do not obey this guard: retire those
   writers explicitly. There is no automatic unfreeze or fallback authority.
2. **Take a pre-switch backup and coherent import export.** Reconcile the latest
   registry/CRL and counters, retain the entire historical `newcerts` set, and
   record a protected coherent backup before web migration. Prepare exactly the
   [PKI import allowlist](pki-service.md#manual-import-only-after-approval), with
   encrypted PKCS#8 CA key and all three endpoint wrapping keys. Do not include
   legacy client/server private keys or old `.ovpn` in the import bundle.
3. **Deploy the reviewed panel under exact approval.** The wrapper's web apply
   quiesces existing API/web/PKI containers before copying code and migrating.
   DB persists; no node restart happens. It installs allowlisted build inputs,
   runtime paths/secrets, migrates through 0006, synchronizes the two VM/agent
   hashes and starts four containers. Migration 0003 logs out everyone and
   disables legacy password access. Interrupted deployment stays in maintenance;
   repair forward without restoring stale CA/CRL or starting old writers.
4. **Import once.** With the separately approved PKI service stop, stage the
   allowlisted bundle outside Git/build contexts, directory `0700`, files `0600`,
   numeric owner 10002. On the approved panel host, with default paths:

   ```bash
   docker compose --file /opt/veilway-control/app/compose.yaml stop pki
   docker compose --file /opt/veilway-control/app/compose.yaml run --rm --no-deps \
     --volume /private/approved-pki-import:/import:ro pki import-ca --source /import
   docker compose --file /opt/veilway-control/app/compose.yaml up --detach pki
   ```

   Each stop/import/start needs the specified approval. Import validates complete
   history, counters, current CRL, encryption/signatures and endpoint keys; it
   refuses an existing committed store. An empty PKI fails closed until import.
   Use fixed readiness/error messages, never dump configuration/material.
   Remove the staging bundle through the approved protected cleanup procedure.
5. **Bootstrap dedicated CRL agents, separately per node.** Use the validated
   wrapper with approved `crl --limit NODE --apply`, initially without timer
   enablement. Bootstrap preserves existing valid CRL/CA and installs isolated
   agent credentials. It does not restart OpenVPN. Agent installation alone
   does not prove mount cutover or revocation enforcement.
6. **Switch dedicated server mounts.** Persist `veilway_crl_agent_managed: true`
   in both private node inventories, including whichever inventory supplies
   the general `deploy/site.yml`. Approve the precise config/container changes.
   Direct, Yandex ingress and AWS transit must use
   `crl-verify /etc/veilway/crl/crl.pem` and directory bind
   `/var/lib/veilway-crl:/etc/veilway/crl:ro`. Never mount the individual CRL file.
   General deployment skips local CRL copying in managed mode; a marker or
   bootstrapped remote CRL blocks accidentally returning to legacy mode even
   from another workstation. Do not remove remote CRL to bypass that guard.
7. **Enable polling only after mount confirmation.** Approve each exact timer
   activation and use `crl --limit NODE --enable-crl-agent --apply`. The role
   checks managed inventory, explicit mount acknowledgement and read-only
   directory mounts. See [full CRL cutover procedure](crl-delivery.md).
8. **Open issuance only after gates pass.** As ADMIN check publisher state,
   correct CA, current signed publication and both node receipts, then perform
   [approved live acceptance](profile-security-acceptance.md#separate-operator-procedure-for-real-vpn-acceptance).
   Do not issue user access before these gates. Verify ADMIN/USER, own-only
   download, all modes/expiry and revoked reconnect behavior. New USER remains
   empty until assigned. Existing legacy profiles enter the cabinet through
   [legacy profile import](legacy-profile-migration.md); server certificate maintenance needs its own reviewed
   authoritative procedure, not the disabled workstation signer.
9. **Take the first post-handover coherent backup.** Preserve the local marker,
   managed inventories, current publication and all acknowledgement evidence.
   Declare server PKI the sole authority. Never redeploy a local CRL or restore
   a pre-handover CA as a quick application rollback.

## Coherent backup

Obtain exact approval for panel maintenance and private backup/export. Use an
encrypted operator-controlled backup volume with `0700` directory/`0600` outputs;
protect account metadata, private profiles, CA registry and receipts as secrets.
Keep CA passphrase and reusable runtime credentials in a separate protected vault.
Do not export them into the same archive or any public artifact.

Quiesce API/web/PKI and all other CA writers; wait for graceful in-flight completion.
Node agents may continue reading an already published valid CRL. Capture their
version/hash/expiry baseline privately; do not lower it later. With default paths
on the approved panel host and a prepared encrypted `/private/veilway-backup`:

For the original three-container panel, stop only `api web` (there is no `pki`
service), then run the same database dump. Its pre-switch backup pairs that DB
with the frozen authoritative operator CA backup/export; do not try to archive
a nonexistent server PKI store. The full `pki.tar` command below applies after
a committed server import. Retain the initial backup for forward reconciliation,
without reopening the disabled workstation writer or legacy password login.

```bash
umask 077
docker compose --file /opt/veilway-control/app/compose.yaml stop api web pki
docker compose --file /opt/veilway-control/app/compose.yaml exec -T db \
  pg_dump --username veilway_control --dbname veilway_control \
  --format=custom --no-owner --no-acl > /private/veilway-backup/control.dump
tar --numeric-owner -cpf /private/veilway-backup/pki.tar \
  -C /srv/veilway-control pki
```

Run only the approved operations; check command exit status and backup integrity
before any resumption. `pg_dump` is consistent for PostgreSQL, but it does not
coordinate with PKI: the common writer freeze is required.
[PostgreSQL 17 pg_dump](https://www.postgresql.org/docs/17/app-pgdump.html).

The PKI archive must include `CURRENT`, its complete referenced generation,
registry/newcerts/counters, private profile files, receipts and endpoint wrapping
keys. Keep ownership 10002, directories `0700`, files `0600`; do not flatten,
re-encrypt individual keys inconsistently or drop receipts. A CA key alone cannot
recover issued profiles or safe retries. PostgreSQL backup includes users and
ADMIN binding, profile owners/jobs/idempotency, restart history, audit, CRL
publications and durable node acknowledgements. Pair archives by the same frozen
maintenance point, source revision and CA/publication evidence.

Retain reviewed code/images/Compose plus private runtime-path settings and managed
inventories. Optional Caddy TLS state is private too; rebuilding ACME state needs
its own approved operation. Verify an isolated restore before considering a
backup usable. Securely handle or remove temporary plaintext archives under
operator policy. Resume only the separately approved panel services afterward.

## Restore and rollback

1. Keep panel writers and issuance stopped. Preserve the current live state
   first; do not overwrite it with a candidate restore. Restore into a fresh
   isolated target, with reviewed image versions and matching migration schema.
2. Restore the paired full PKI archive and DB dump with original numeric
   permissions and separately provisioned protected secrets. Do not run
   `import-ca` over a restored committed store. With a verified empty candidate
   database already initialized under the approved matching Compose setup:

   ```bash
   docker compose --file /opt/veilway-control/app/compose.yaml exec -T db \
     pg_restore --username veilway_control --dbname veilway_control \
     --single-transaction --exit-on-error --no-owner --no-acl \
     < /private/veilway-backup/control.dump
   ```

   This is a candidate-target command, never an instruction to overwrite a live
   database. Do not add `--clean`, downgrade migrations or delete publications.
   [PostgreSQL 17 pg_restore](https://www.postgresql.org/docs/17/app-pgrestore.html).
3. Reconcile candidate CA fingerprint, complete revoked set, registry/newcerts,
   highest used serial and CRL counter against the private latest baseline and
   node-installed versions. Verify profile owner/UUID/job keys, publication
   hashes, receipts and pinned ADMIN subject. Account for every post-backup issue,
   revoke, assignment and receipt. If completeness cannot be proven, keep
   stopped; an old backup cannot reconstruct unknown newer CA operations safely.
4. Never lower counters or remove a revoked serial. Agents reject rollback,
   same-version changed bytes and incomplete revoked sets. A stale candidate
   must be reconciled using authoritative current data under a reviewed repair;
   changing agent state to accept a rollback is not recovery. Local handover
   markers and managed inventories remain enabled throughout.
5. Invalidate restored browser sessions/OAuth attempts under a specifically
   approved DB operation before reopening the candidate. Preserve `users` and
   `google_admin_binding`; never bootstrap a different ADMIN merely by editing
   email. Lost operator Google identity requires a separate reviewed account
   recovery procedure. Rotate a compromised agent/provider credential separately
   and synchronize both sides; reissuing unrelated tokens silently breaks delivery.
6. Validate the candidate through the same local checks and approved acceptance,
   then separately approve service startup/cutover. Resume durable jobs with
   their original keys: do not create substitute profiles for ambiguous outcomes.
   After node acknowledgements, take a new coherent backup. An application-only
   rollback may reuse a compatible reviewed image with the same newest DB/PKI
   state; it must not roll back cryptographic state or revive legacy password login.

## Local release evidence and limits

`scripts/check.sh` includes three temporary rollout tests: no-contact input
validation/redaction, flock handover and real Ansible check-mode behavior for
stale CRL copying/legacy guards. The Ansible fixture includes only authority/PKI
tasks, uses synthetic files and localhost with `--check`; it does not run host,
network or service phases. Do not run a general production `--check` against
real nodes casually: fact gathering still connects to them.

`scripts/test-pki-service.sh --build` now passes 18 tests, including coherent
store restore with all modes, receipts, unchanged download/counters/CRL and
continued monotonic issue/revoke operations. `scripts/test-control-plane.sh --build`
uses the full backend/PKI/agent/PostgreSQL suite and then exercises
the actual production API image/migrations/VM sync against disposable Compose.
Its PostgreSQL smoke also runs actual custom-format `pg_dump`/`pg_restore` and
checks restored ADMIN binding, revoked profile, node acknowledgement and schema.
All materials are temporary synthetic fixtures; these checks do not authorize
or prove actual production backup, Google setup, DNS/TLS or VPN routing.

Run release checks from a public clean-input checkout:

```bash
scripts/check.sh
scripts/test-control-plane.sh --build
scripts/test-pki-service.sh --build
scripts/test-profile-panel.sh --build
python3 scripts/check-public-diff.py --all-public --history
```

Docker test builds explicitly obtain dependencies/base images; preload the
fixed `postgres:17.4-alpine` fixture if missing. ShellCheck is checked only if
already installed. See [stage 7 evidence and live procedure](profile-security-acceptance.md)
for browser/proxy security checks and residual live acceptance. Development
completion, PR publication, deployment and live acceptance are separate statuses.
