# Profile API and durable PKI jobs (stage 4)

The public API now connects Google ADMIN/USER sessions to the isolated PKI.
It manages newly issued UUID profiles and explicitly imported historical profiles
through the same ownership, expiry, download and revocation rules. The
[legacy migration procedure](legacy-profile-migration.md) is operator-only;
the public API cannot upload private materials. Existing OpenVPN Access Server
remains outside this API. The [browser profile cabinet](profile-panel.md) is implemented in
stage 6; [CRL distribution and node acknowledgements](crl-delivery.md) are implemented in stage 5.

## Endpoints

All paths have the `/api/v1` prefix. Every mutation requires the authenticated
HttpOnly session and `X-CSRF-Token` from `/auth/session`. Metadata and download
responses use `Cache-Control: no-store`. Lists accept `limit` (1–100, default
100) and `offset` (>=0), with stable ordering.

| Method/path | Access | Result |
| --- | --- | --- |
| `GET /users` | ADMIN | Active registered USER IDs/emails for owner selection |
| `GET /profiles` | ADMIN/USER | All managed profiles for ADMIN; own profiles for USER |
| `POST /profiles` | ADMIN + CSRF | `202`, profile metadata and durable issue job |
| `GET /profiles/{id}` | ADMIN/owner | Profile metadata; inaccessible IDs return `404` |
| `PATCH /profiles/{id}` | ADMIN + CSRF | Change only `device_name` |
| `POST /profiles/{id}/owner` | ADMIN + CSRF | Assign an unassigned profile once |
| `POST /profiles/{id}/download` | ADMIN/owner + CSRF | Active `.ovpn` attachment |
| `POST /profiles/{id}/revoke` | ADMIN + CSRF | `202`, durable local revoke job |
| `GET /profile-jobs` | ADMIN/USER | All jobs for ADMIN; own profiles' jobs for USER |
| `GET /profile-jobs/{id}` | ADMIN/owner | Job state or `404` |
| `GET /profile-audit-events` | ADMIN | Actor/action/object/result/time journal |

`GET` downloads are not supported. Unknown or foreign profile/job IDs return
`404` for authenticated USER reads/downloads, without consulting PKI. USER
cannot create, rename, assign or revoke profiles, enumerate users or read the
administrative journal. Unauthenticated requests receive `401`; administrative
operations by USER and missing/incorrect CSRF receive `403`.

Create JSON requires a fresh `idempotency_key` UUID, `device_name` (1–128
characters), and `mode` (`yc-direct`, `aws-direct`, `yc-aws-multihop`). Optional
`owner_id` must reference an active Google-registered USER. Omitting it issues
an unassigned profile, downloadable by ADMIN. Supply either `duration_days`
(positive integer within the supported calendar) or `expires_at` (ISO timestamp with explicit timezone, whole
seconds). Omitting both means 365 days. Past expiries and conflicting fields
are rejected with `422`. The requested expiry is checked against the actual
CA by PKI before signing: a request beyond its expiry fails the job with
`pki_expiry_rejected`, without consuming a committed certificate serial. It is
not silently shortened. The asynchronous API can accept the job before that
CA check; poll job status before offering a download.

Unknown input fields are forbidden. UUID, role, profile status, PKI paths,
certificate metadata and authors cannot be supplied through rename/assignment
bodies. Display names accept Unicode but reject blank/control-character input.
The certificate CN and download filename use the immutable generated profile
UUID, never the device name. Multiple profiles may share a name and mode.

Repeating the same create key/payload returns the same profile and job. Reusing
it for another actor/payload/operation returns `409`. Assignment JSON contains
only `owner_id`; a repeat to that owner is allowed, but changing or clearing an
assigned owner is rejected. This remains true after revocation. To change users,
request revocation and create a new profile. Copies previously downloaded by the
old owner cannot be recalled by changing database metadata.

Revoke JSON contains only `idempotency_key`. Repeats return the original revoke
job, including repeated browser requests with fresh keys. At most one issue and
one revoke job exist per profile, enforced by a database unique constraint.
Profiles still issuing or with a failed issue cannot be revoked through this
version's API; ambiguous PKI conflicts require operator investigation rather
than silently minting another certificate.

## Download and revocation states

A download requires both active database metadata and an unexpired, unrevoked
PKI record. It returns `application/x-openvpn-profile`, an attachment filename
`veilway-<uuid>.ovpn`, `no-store`, `Pragma: no-cache`, `nosniff` and
`Referrer-Policy: no-referrer`. Repeated downloads return the same material.
Keys/profile bytes are transient response data, never database or audit fields.
A pending/failed/expired/revoking profile is rejected with `409`; unavailable
or inconsistent PKI returns sanitized `503`, with no private material.

The revoke transaction immediately changes profile state to `revoking`, so
subsequent downloads stop before the worker contacts PKI. The worker then records
the signed CRL version and marks the *local PKI job* `succeeded`. The profile
stays `revoking` until the CRL worker verifies its relevant VPN node installed a
valid full CRL containing that certificate; it then changes to `revoked`. Active
VPN sessions are not forcibly terminated. A database-active profile past its expiry
is returned as `expired`
even before any background update.

## Worker, transactions and audit

`main.lifespan` runs the profile worker alongside the existing restart worker.
A claim locks an eligible job with PostgreSQL `FOR UPDATE SKIP LOCKED`, then
commits `running`, a random claim token, attempt count and a 180-second lease.
A guarded update prevents duplicate claims. The PKI call runs without holding
that database transaction and always uses the job's original idempotency UUID.
PKI serializes its own store and persists its receipt before replying.

Completion locks the job/profile and checks the current claim token before
writing metadata. An old worker cannot overwrite a result saved after lease
reclamation. If the service/process/DB fails after CA commit, the next claim
replays the same PKI receipt. Transient socket/storage/operation errors requeue
with bounded exponential delay (up to 256 seconds); they do not invent another
job key or assert that signing did not happen. Invalid expiry/request is a
terminal issue failure. Inconsistent/conflicting outcomes become `needs_review`
with fixed error codes, without disclosing PKI responses or exception text.

Owner assignment and download checks lock the profile row. Concurrent owner
assignments are serialized; only the first assignment succeeds. Profile creation,
job enqueue and accepted audit record commit together. Replays do not duplicate
that create record. Successful/denied/unavailable downloads and authorized
rename/assign/revoke requests record their actor, action, object ID, result and
time. Worker completions attribute the action to the requesting ADMIN. The
journal contains no email copies, labels, `.ovpn`, keys, provider credentials or
raw exceptions. It is exposed only to ADMIN and has no mutation endpoint.

PostgreSQL stores certificate serial/digest, job lease/retry/CRL metadata and
the action journal; CA/client keys and profiles remain solely in PKI storage.
Migration `0004_profile_api` adds those fields and constraints, keeps existing
IDs/ownership/history and requeues pre-worker `running` jobs lacking leases.
A downgrade refuses to discard active claims, new job receipts, certificate
metadata or action history. Back up DB/PKI coherently and apply migrations only
under the separate deployment approval described in [PKI operations](pki-service.md).

Locking references: [PostgreSQL row locks](https://www.postgresql.org/docs/17/explicit-locking.html)
and [SQLAlchemy FOR UPDATE](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Select.with_for_update).

## Verification

```bash
scripts/test-profile-api.sh --build
scripts/check.sh
```

The first command builds the backend test distribution and a separate integration
image containing real OpenSSL/OpenVPN and the public PKI sources. It runs all
backend and HTTP/socket/CA integration tests with synthetic material in tmpfs,
read-only root and no network. PostgreSQL-only tests skip unless an explicit
`VEILWAY_TEST_POSTGRES_URL` to a disposable database is supplied. For those tests,
run that image with the temporary PostgreSQL container's isolated network namespace
and this variable; do not target production. Tests create/drop their own schemas.

The stage-4 validation additionally uses disposable PostgreSQL for migration,
duplicate create, competing owner assignments/revokes, concurrent job claims,
lease reclamation and late-worker fencing. Integration checks cover all three
modes through HTTP -> worker -> Unix socket -> actual synthetic CA, owner-only
repeatable download, local revocation and a CA shorter than the default duration.
Mock Google sessions avoid contacting Google or using real operator credentials.
