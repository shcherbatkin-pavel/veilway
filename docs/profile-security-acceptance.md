# Profile security and integration acceptance (stage 7)

Local acceptance completed on 2026-10-05. This records development checks;
production deployment and live acceptance have not been performed.

## Repeatable local checks

Run from the repository root with Docker available:

```bash
scripts/test-profile-security.sh --build
scripts/test-pki-service.sh --build
scripts/test-profile-panel.sh --build
docker build --tag veilway-control-web:stage7 web/frontend
docker build --file web/frontend/Dockerfile.browser --tag veilway-profile-panel-test:stage7 web/frontend
python3 scripts/test-panel-proxy.py
scripts/check.sh
python3 scripts/check-public-diff.py --all-public --history
```

The PostgreSQL fixture image `postgres:17.4-alpine` must already be available
locally; explicitly preload it with `docker pull postgres:17.4-alpine` if needed.
The test run uses `--pull never` for this fixture.

Builds explicitly download pinned dependencies/base images when needed. Tests
do not contact Google, clouds or VPN hosts. The security wrapper creates a
disposable PostgreSQL with `--network none`, no published ports, no host data
mounts and database storage in tmpfs. The test container shares only that
container's isolated loopback. An EXIT trap stops and removes PostgreSQL.
An interrupted host or killed wrapper may require stopping its specifically
named `veilway-security-postgres-*` test container; do not stop other containers.

The production frontend build performs typecheck, unit tests and bundle build.
Browser acceptance serves the compiled bundle on isolated loopback with API
fixtures. The proxy test parses the actual production Caddy configuration,
then changes only its site address to isolated HTTP loopback to avoid ACME.
It uses the production `NET_BIND_SERVICE` capability required by the Caddy
binary, no host ports or mounts, and stops its temporary container in finally.

## Requirement evidence

| Requirement | Authoritative local coverage |
| --- | --- |
| Roles and infrastructure isolation | `test_access.py`: unauthenticated/USER/ADMIN matrix, forged role header, legacy password endpoint disabled, live role/deactivation checks; `test_profiles.py`: owner/stranger/unassigned profiles and jobs |
| Mass assignment | Strict profile request schemas; create validation cases and `test_stage7_security.py` reject extra rename/assignment/revoke fields without changing profile/job state |
| CSRF and session substitution | Session-bound CSRF hashes; missing/wrong/cross-session tokens fail, owner token still works, inactive identity rejected |
| OAuth replay and token substitution | `test_oidc.py`: browser-bound single-use state, expiry/replay, actual RSA signature verification, issuer/audience/nonce/expiry/claims, algorithm/key rejection, fixed callback, pinned ADMIN subject |
| Concurrent issue and replay | `test_profile_postgres.py` uses real PostgreSQL row locks: duplicate create, competing owner assignment, single worker claim, revoke replay, expired lease fencing; PKI tests exercise concurrent serial allocation and repeat issue |
| Crash recovery and outages | Worker loss after PKI/before DB commit reuses the durable key; real PKI process crashes before/after commit; transient PKI retry and database outage recovery; CRL lost-ack retry and pending-node gating |
| Secret persistence | All tables of disposable OAuth/profile integration databases inspected for actual synthetic ID/access/refresh tokens, client secret and downloaded private VPN material; only hashes/metadata persist in these flows |
| Error/log disclosure | Captured fixture logs exclude tokens/private profile blocks; PKI framing/metadata reject unexpected material; validation responses contain a fixed message instead of rejected input; production Uvicorn access logs disabled; actual Caddy requests with CSRF/cookie/OAuth canaries do not leak into runtime/access logs |
| Cache and browser storage | API success and handled 401/403/404/409/422 responses have `no-store`; actual Caddy gateway 502 has safe headers and fixed body; browser requests use `cache: no-store`, no local/session storage, IndexedDB, CacheStorage or service worker registrations; Blob URL is revoked; logout/reload removes profiles |
| Git and local artifacts | Ignore-policy checks plus credential-pattern scan of all public working files and objects reachable from local Git refs; staged diff empty at acceptance; source/diff review |
| VPN revocation | Backend suite runs actual synthetic OpenVPN TLS reconnect with loopback/`dev null`: revoked certificate rejected, another accepted, same server PID; production routing deferred to the procedure below |

Final local results: **164 backend/integration tests passed without skips**,
**17 PKI smoke tests passed**, **13 frontend unit tests passed**,
**24 Chromium scenarios passed** at 1440/390/320 px, production build and proxy
check passed, `scripts/check.sh` passed. Four Terraform roots and seven Ansible
playbooks passed local validation/syntax checks. ShellCheck was unavailable and
explicitly skipped; it was not installed. Two third-party TestClient/AnyIO
deprecation warnings remain and do not affect these assertions.

The public-file/history scanner checks known private-key/certificate blocks,
wrapped OpenVPN key blocks and common AWS/GitHub/Google token forms. It emits
only paths/object IDs, never matches. It is a heuristic, not proof that arbitrary
passwords or unfamiliar secret formats cannot exist. Only reachable local Git
refs are covered; ignored operator files, unreachable objects, external copies
and remote-only history are outside this inspection. Do not read those files
or publish audit output to extend this audit implicitly.

Application data intentionally includes user email, profile metadata, hashed
session/agent credentials and signed public CRL/CA publication data. These are
not private keys or reusable provider credentials. Private CA/client material
belongs to isolated PKI storage and the authenticated download response.
User-supplied profile names are metadata; operators must not paste secrets into
names. Browser download tests deliberately create a harmless file: saving an
authorized `.ovpn` to a user's Downloads folder is expected behavior, distinct
from browser/application cache. Protect that file under operator/user policy.

## Separate operator procedure for real VPN acceptance

This procedure is a deliverable, not authorization to execute it. Before any
real operation obtain exact approval for the selected environment, accounts,
profile issuance/revocations, connections and any planned fault injection.
Deployment, Google setup, CA import, CRL-agent installation and mount cutover
must already have their own approvals and follow [CRL delivery](crl-delivery.md)
and [PKI](pki-service.md). Do not connect automatically or inspect host journals,
service environments, private keys, existing VPN directories or cloud metadata.
The existing OpenVPN Access Server is outside the procedure and stays unchanged.

1. Select the separately deployed Veilway panel and dedicated new VPN nodes.
   Confirm the approved CA/CRL bootstrap and agent state through the ADMIN API.
   Keep actual addresses, account email and evidence in protected operator
   records, outside this public repository. Do not include profiles/tokens in
   screenshots or HTTP captures.
2. Sign in with the approved operator Google account: it must be ADMIN. Sign
   in separately with two test Google accounts: both must be USER. A fresh USER
   must have an empty cabinet and no infrastructure sections or requests.
   Confirm Google requests only identity scopes, without Gmail mailbox access.
3. ADMIN issues two temporary profiles per mode (`yc-direct`, `aws-direct`,
   `yc-aws-multihop`) for the first test USER, with a short duration within the
   remaining CA lifetime. Confirm issuance reaches active, owner/scope/expiry
   match, downloads repeat, and the second USER cannot list/detail/download
   these profiles or their jobs. Direct checks use protected local tools and
   valid own-session CSRF, never copied operator cookies.
4. On approved disposable client devices connect each mode separately. Check
   the expected egress and private DNS behavior against the approved network
   design, DNS leak expectations, transit path and application traffic. Do not
   alter node routes/firewalls or reuse existing Access Server profiles.
   Loopback TLS tests do not prove these packet-routing properties.
5. Revoke one profile per mode. Download must be blocked immediately while
   the UI says «Отзыв применяется». Final `revoked` must wait for the required
   node's valid acknowledgement: AWS for AWS Direct; Yandex for Yandex Direct
   and Multi-hop. After acknowledgement disconnect that test client and attempt
   a new connection: it must fail. The second, unrevoked profile must reconnect.
   Existing sessions are not forcibly disconnected by CRL delivery; a successful
   session established before revocation is not evidence of a reconnect defect.
6. If explicitly approved for a staging environment, exercise a PKI outage,
   unavailable CRL target and worker loss. Record issuing/queued or revoking
   behavior, then recovery with the same profile/job and complete monotonic CRL.
   Otherwise leave this live fault-injection check pending; local coverage above
   is the evidence for development acceptance. Never stop production services
   or nodes just to simulate these conditions.
7. Exercise a short-lived profile until expiry, then confirm expired status,
   denied download and rejected new VPN connection. Log out and reload the
   browser; profiles must disappear. Inspect only this test browser's storage
   and response headers for cache/storage behavior. Explicitly requested
   downloads must be removed from test devices under operator policy.
8. Revoke remaining temporary profiles and wait for acknowledgements. Remove
   downloaded test material securely under operator policy. Preserve the CA
   registry, revoked serials, latest CRL and durable acknowledgements: temporary
   acceptance profiles must not be undone by rolling back or deleting history.
   Record each check as passed/failed/pending, with time and profile UUIDs only
   in private operator evidence. Stop acceptance on a mismatch and repair under
   a separate approved operation; do not restart existing Access Server or
   silently weaken CRL enforcement.

Node restarts are not required by this live procedure. Restart UI behavior is
covered with fixtures; any real restart needs approval for the exact node/action.
Actual Google/provider integration, TLS termination/ACME, routing/DNS, operator
backup/recovery and dedicated-node deployment remain live acceptance concerns.
Stage 8 provides the [rollout and recovery runbook](profile-rollout.md).
The test counts above record stage-7 acceptance; stage 8 adds rollout and
backup/restore checks without performing production deployment.

Caddy's error-route behavior follows its official
[handle_errors documentation](https://caddyserver.com/docs/caddyfile/directives/handle_errors).
