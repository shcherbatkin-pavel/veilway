# Restart control plane operator guide

The implementation under `web/` provides Google sign-in, ADMIN/USER sessions,
and administrative restart control for `aws-direct` and `yc-direct`.
The isolated PKI and public profile API are implemented in stages 3–4;
Signed [CRL delivery](crl-delivery.md) is implemented; [the browser cabinet](profile-panel.md) is implemented. None of the commands in this guide are run by the
repository or test suite automatically.

For the current release use [profile rollout, handover and recovery](profile-rollout.md)
and [ADR 0005](adr/0005-google-profiles-and-server-pki.md). Historical stage
descriptions below do not authorize reverting to password login or local CA writes.

## Components and API

The steady state is four containers, including the network-isolated PKI:

```text
Internet -> Caddy + React -> Unix socket -> FastAPI -> PostgreSQL
                                           |       -> AWS EC2 API
                                           |       -> private Unix socket -> PKI
                                           `-------> Yandex Compute API
VPN nodes ------------ outbound heartbeat --------> FastAPI
```

The versioned API provides login, session and logout endpoints, the two-VM
status list, restart job creation/history/detail, and authenticated agent
heartbeats. The schema contains `User`, `UserSession`, `VpnVm`,
`VmHeartbeat`, `RestartJob`, `RestartTarget`, `VpnProfile`, `ProfileJob`,
`OAuthLoginAttempt`, and `GoogleAdminBinding`.
Profile models store metadata only; the PKI keeps private material in exclusive
storage. The [profile API](profile-api.md) connects it to PostgreSQL jobs;
[CRL delivery](crl-delivery.md) is implemented; deployment and real-node acceptance require separate exact approval.
Responses never include
instance IDs, IP addresses, heartbeat tokens, credentials or cloud request
IDs.

### User model foundation (stage 1)

This section describes migration 0002. Stage 2 below supersedes its password
login behavior; the current backend has no password login endpoint.

Migration `0002_users_and_profiles` renames the legacy administrator/session
tables and restart-author column in place. It preserves UUIDs, password hashes,
session hashes and restart history. Existing identities become active `ADMIN`
users with no Google subject or email; new identities default to `USER`.
Google identities are unique by subject, never automatically matched to a
legacy login or an email alone. Google sign-in is not enabled by this stage.

Password login, session and logout remain available. VM status and all restart
endpoints require `ADMIN`; mutations also require CSRF. Role and active status
are checked against the database on every request. There is no public role
mutation endpoint. Shared profile queries filter by owner in SQL for `USER`
and return `404` for inaccessible or absent profiles; the public profile API
will be added in stage 4.

The `bootstrap-admin` CLI remains restricted to legacy identities. Changing
the operator login disables previous legacy identities instead of deleting
historical job authors. Credential changes revoke legacy sessions while
preserving Google users and their sessions. Routine redeployment with unchanged
credentials preserves sessions.

Applying the migration to an existing installation is a separately approved
operator action. Before applying, back up the database and deploy the matching
backend code with the migration. Downgrade to the old schema is allowed only
while all users are active legacy administrators and the profile tables are
empty; otherwise it fails without deleting data or reactivating disabled
credentials. Later rollback requires an operator migration that preserves
identities, profiles and revocations.

Backend tests use SQLite with foreign keys enabled. Migration tests additionally
require an explicitly supplied `VEILWAY_TEST_POSTGRES_URL` pointing only at a
disposable local PostgreSQL 17 database. Each test creates and removes its own
schema; never supply a production database URL. Without that test URL the
migration tests are reported as skipped, not passed.

### Google registration and sign-in (stage 2)

The browser navigates to `GET /api/v1/auth/google/start`, then Google returns
to `GET /api/v1/auth/google/callback`. The server exchanges the authorization
code, validates the ID token using pinned PyJWT with Google's fixed HTTPS JWKS
endpoint, and redirects to `/` with a new server-side session. It requests only
`openid email`, never Gmail/Drive access or offline access. See
[Google OIDC](https://developers.google.com/identity/openid-connect/openid-connect).

Login attempts last at most ten minutes. PostgreSQL stores only state, nonce
and browser-cookie hashes; callback consumes an attempt atomically before
exchanging the code. OAuth uses a separate Secure, HttpOnly, `SameSite=Lax`
`__Host-veilway_oauth` cookie; the session remains Secure, HttpOnly and
`SameSite=Strict`. Invalid, expired, duplicate or replayed callbacks fail with
a fixed error redirect. Google access/refresh/ID tokens are not persisted.

First login creates a Google identity by `sub`. The configured administrator
email must be verified and Google-authoritative (Gmail or the matching Workspace
hosted domain); arbitrary third-party email ownership is insufficient for
admin bootstrap. See [Google's email authority guidance](https://developers.google.com/identity/sign-in/web/backend-auth).
The singleton database binding is locked during account resolution and pins
ADMIN to the first matching Google identity. All other identities receive USER,
even if their email later matches the configured admin address. A bound account
retains its identity and role when its verified email changes; editing the
configuration alone does not transfer admin rights. Administrator replacement
requires a separately designed operator procedure; there is no public role API.

`GET /api/v1/auth/session` returns `user_id`, `email`, `role`, and `csrf_token`
with `Cache-Control: no-store`. Logout requires CSRF. USER sees an account
screen and does not load infrastructure APIs; ADMIN retains the dashboard.
The [profile cabinet](profile-panel.md) is implemented in stage 6.

Migration `0003_google_sign_in` invalidates all prior sessions, disables legacy
password identities and resets any preexisting Google roles to USER. Legacy
UUIDs, password hashes and restart authors remain in the database. The old
`POST /api/v1/auth/login` endpoint is removed; the legacy `bootstrap-admin`
CLI is not used by deployment and cannot grant access through Google. Downgrade
does not restore sessions or reactivate passwords; once the admin is bound,
downgrade refuses to discard the binding.

Caddy skips OAuth access logs; both access and runtime error logs omit request objects.
The backend entrypoint already disables Uvicorn access logging. Do not enable
raw request, token-response or debug-body logging for authentication.

#### Prepare Google inputs before an approved deployment

1. After explicit approval, create a Google OAuth client of type **Web
   application** and configure its consent screen/audience. Set the authorized
   redirect URI to `https://veilway.ru/api/v1/auth/google/callback` exactly.
   Make its audience available to intended users before live registration
   acceptance. Basic identity scopes have an exception to the Testing allowlist;
   do not treat it as access control. See the current [rollout guide](profile-rollout.md).
2. Update the protected local `.env` to the current `.env.example` contract:
   replace `ADMIN_LOGIN`/`ADMIN_PASSWORD` with `GOOGLE_CLIENT_ID`,
   `GOOGLE_CLIENT_SECRET`, and `ADMIN_GOOGLE_EMAIL`. Use the operator's actual
   Google email privately; matching is case-insensitive, without Gmail dot or
   plus-alias rewriting. Retain the other infrastructure inputs.
3. The deployment wrapper validates without contacting hosts unless `--apply`
   is passed. After separately approved deployment, three additional root-only
   runtime files are installed: `google_client_id`, `google_client_secret`,
   `admin_google_email`. Compose passes file paths only; the entrypoint loads
   them into sealed descriptors before dropping privileges.
4. Back up the database and review migration 0003 before applying it with the
   matching backend/frontend images. Switching logs everyone out. Verify the
   operator's first Google login yields ADMIN and another account yields USER.
   Verify legacy password login fails and history remains readable by ADMIN.

No Google project, real secret, host or deployment is changed by local tests.

## 1. Provision in separately approved stages

Review and apply `infra/yandex-web` separately. It creates the new public web
VM, isolated network, TCP/22+80+443 security group, static IPv4, public Cloud
DNS zone with the `veilway.ru` apex A record, service account and persistent
data disk attached with `auto_delete=false`. Its instance-level IAM binding must name only
the existing `yc-direct` ID. Before applying, inspect existing
`compute.operator` access bindings on that VM: the Terraform binding is
authoritative for that role.

Because that role is intentionally scoped to one instance, the backend checks
Yandex restart completion through the instance-specific Compute API operations
list. It must not use the global operation endpoint or broaden the service
account binding to the folder merely to read operation status.

Review and apply `infra/aws-management` separately. It creates an IAM user and
policy but no access key. The planned `RebootInstances` resource must be the
single `aws-direct` ARN and status reads must be limited to one region. Create
the access key manually only after that policy has been reviewed; store it in
the local `.env`, never in Terraform state or command arguments.

After applying the Cloud DNS resources, delegate `veilway.ru` at REG.RU to the
authoritative name servers returned by the Terraform `dns_name_servers`
output. Do not change registrar NS records before the public zone and apex A
record exist. Wait until public resolution is correct before deploying Caddy
so production ACME validation can succeed.

## 2. Prepare protected local inputs

Copy `.env.example` to `.env`, replace every placeholder, and protect it:

```sh
chmod 0600 .env
cp deploy/control-inventory.example.yml deploy/control-inventory.yml
```

Replace the documentation-only addresses in the ignored inventory. The
`control_web` host must be only the new web VM. The `direct_vpn` group must
contain only `aws-direct` and `yc-direct`, with an explicit list of containers
whose health the outbound agent will inspect.

The wrapper rejects unknown `.env` keys, missing values, an unsafe file mode,
or a file not ignored by Git. Without `--apply` it validates locally and does
not contact any host:

```sh
python3 scripts/deploy-web-control.py web
python3 scripts/deploy-web-control.py heartbeat
```

## 3. Deploy only after explicit approval

The following are two distinct mutating operations. Run each only after its
inventory and scope have been approved:

```sh
python3 scripts/deploy-web-control.py web --apply
python3 scripts/deploy-web-control.py heartbeat --apply
```

The web role formats only the Terraform-attached empty `virtio-data` disk,
mounts it at `/srv/veilway-control`, installs six root-owned `0400` application
secret/input files, runs Alembic, synchronizes the exact two VM
records through protected stdin, and starts the four containers. The two PKI-only
runtime inputs use root-owned `0600` files. PKI requires a separately approved
manual import before it can serve; see [PKI service](pki-service.md). It never
copies the complete `.env`.

On upgrade the role first stops existing API/web and, if present, PKI before
replacing application code or migrating. This supports both the original
three-container panel and the current four-container panel. Take the approved
coherent backup first. The deployed project `.env` stores only path interpolation,
not credentials. Follow the [ordered rollout](profile-rollout.md) for CA/agent cutover.

The PostgreSQL password initializes the database cluster and must remain
unchanged during routine redeployments. Rotating it requires a separate,
explicit database-password procedure; merely editing `.env` intentionally
causes migration to fail rather than silently desynchronizing the database
role and API secret.

The heartbeat role installs only a small read-only Docker inspector and a
15-second systemd timer. It reads boot ID and uptime from `/proc`, reads the
status of the explicitly listed containers, and sends one outbound HTTPS
request. It has no listener or command endpoint and does not change or restart
OpenVPN. To install one node separately, append `--limit aws-direct` or
`--limit yc-direct`.

## 4. Acceptance before a real restart

Before using the restart button, verify all of the following manually:

- `https://veilway.ru` is reachable without either VPN;
- the browser receives a valid certificate and the API/database expose no TCP
  ports;
- both dashboard cards have fresh healthy heartbeats;
- the AWS credentials pass an operator-run `RebootInstances` DryRun for only
  `aws-direct` and cannot target another instance;
- the Yandex service account has no folder role and its instance binding names
  only `yc-direct`;
- the web VM and the legacy OpenVPN Access Server are absent from the managed
  VM table and all IAM target lists.

A real restart is a further explicit action in the UI. For both targets, the
job waits up to 15 minutes for AWS to return with a new healthy boot ID before
dispatching the Yandex restart. `failed` stops the sequence. `needs_review`
means the cloud mutation outcome was ambiguous and the worker deliberately did
not retry it.

## Local verification

The normal test suite uses SQLite plus mocked cloud providers and cannot call
real cloud mutations. Run it in the dedicated test containers; `--build` is an
explicit acknowledgement that Docker may fetch the pinned base images and
package dependencies when they are not cached locally:

```sh
./scripts/test-control-plane.sh --build
```

The runner executes backend pytest without network access or a writable root
filesystem, builds the pinned frontend stage, validates the Compose exposure,
and then runs the disposable PostgreSQL integration check. To run only the
PostgreSQL check (it builds images and creates then removes local containers),
use:

```sh
python3 scripts/control-postgres-smoke.py
```

## Worker readiness and recovery

`GET /api/healthz` remains a liveness check returning `{"status":"ok"}`.
`GET /api/readyz` returns HTTP 200 with `{"status":"ready"}` only while all
three background worker tasks have started and remain running. Before startup,
during shutdown or after an unexpected task exit it returns HTTP 503 with
`{"status":"unavailable"}`. This checks task lifetimes, not database, cloud or
PKI availability; transient outages handled inside a running worker do not by
themselves make readiness fail. Responses contain no exception details.

Unexpected restart-worker failure is not retried automatically. An operator must
inspect the durable job state and any ambiguous dispatch before explicitly
approving recovery/restart. On the next approved application startup, interrupted
dispatches are marked for review rather than rebooted again. PKI workers retain
their original job keys and lease recovery; CRL polling retains its own retry
policy. Application shutdown signals every worker and waits for all in-flight
operations, including threaded PKI calls, without cancelling them.
