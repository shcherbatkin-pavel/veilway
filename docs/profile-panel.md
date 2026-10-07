# Profile panel (stage 6)

The browser panel has a responsive dark Veilway interface. ADMIN starts in
“Профили” and can open “Пользователи”, “Узлы” and “История”; USER starts in
“Мои профили” and has no administrative navigation or administrative API reads.
The backend remains the authority for roles, ownership, CSRF and profile state.
This development stage does not deploy the panel or contact real VPN nodes.

## Profiles and owners

ADMIN can search by device name, mode or owner email, filter by mode/status/owner,
create a profile with a registered USER or assign its owner later, rename a
profile, download an active profile and request revocation. The form defaults to
365 days; PKI checks the actual CA lifetime asynchronously. The panel explains
failed issuance and a requested lifetime beyond the CA, rather than displaying
a failed job as an active downloadable profile. New users do not receive a
profile automatically merely by registering through Google.

Existing profiles adopted through the [operator migration](legacy-profile-migration.md)
use these same screens and ownership rules, retain original expiry and download
bytes, and start without owners. History labels the adoption as an import.

A create request uses one UUID idempotency key and an immutable submitted payload
across uncertain responses. If the response is lost, the dialog retains its
parameters and retries the same request, even after closing and reopening it.
It does not silently generate a second key and profile. Validation errors allow
correction. Double submissions are blocked while the operation is pending.
Assigning an already assigned profile to a new owner is intentionally unavailable;
revoke the old profile and create a new one for the new owner. The user list links
to that user's profiles and supports email search.

Revocation requires an explicit dialog. Cancellation performs no mutation. After
acceptance, downloads are disabled while the relevant VPN node applies its CRL.
The panel shows “Отзыв применяется”, with automatic refresh, until the backend
reports “Отозван”. It explains that active sessions are not forcibly terminated.
Issuing, active, expired, revoked and failed profiles have distinct text labels;
status is never communicated by color alone. An active profile whose expiry has
passed also becomes unavailable in the browser, without waiting for a poll.

USER sees only the metadata returned by the owner-scoped endpoints: device name,
VPN mode, status, expiry and download action. An empty account explains that the
administrator has not assigned profiles yet. USER does not request users, VPN
nodes, restart history, administrative audit events or CRL delivery details.
Issuance problems ask USER to contact the administrator rather than offer controls
that the user cannot perform.

## Nodes, history and failure states

“Узлы” retains individual/selected and ordered bulk restart operations with an
explicit confirmation and existing health/active-job gating. “История” contains
profile audit actions and the existing recent restart history. Restart state and
errors are translated into understandable Russian labels. Profile history has
search and action filters. “Узлы” also displays CRL publication problems and
per-node installation/offline/expiry state from the authenticated delivery API.

Profile, user and audit lists traverse backend pagination; profiles beyond the
first hundred remain accessible. Reads poll every five seconds without overlapping
request groups. Leaving a section aborts its reads and suppresses late results.
Loading, empty and filtered-empty views are separate from unavailable-data views.
A failed refresh preserves the last snapshot with a warning. A `401` removes the
account view and requests Google sign-in again. Initial session/network failure
has a retry screen and does not pretend the account is logged out. Error messages
use fixed local strings and do not render raw server error bodies.

Mutation requests send the session CSRF token. Downloads use authenticated POST
and a temporary Blob URL with the immutable profile UUID filename. Profile bytes
are never rendered, logged, or stored in local/session storage. Object URLs are
revoked after dispatch. No analytics, remote fonts, image services or audit
exports are added. Native modal dialogs contain keyboard focus, support Escape
when idle and disable dismissal while the request is pending.

The sidebar becomes a mobile header/navigation; profile rows become labeled
cards. Action controls remain accessible on touch screens and long owner emails
wrap or truncate appropriately. No horizontal document scrolling is needed at
1440, 390 or 320 pixels. Local synthetic screenshots were inspected separately;
production/user data must not be recorded in repository screenshots.

## Verification

```bash
npm --prefix web/frontend run typecheck
npm --prefix web/frontend run test
npm --prefix web/frontend run build
scripts/test-profile-panel.sh --build
scripts/check.sh
```

The browser script builds an isolated test image with the compiled production
bundle and Playwright 1.63.0. A loopback HTTP server and Chromium run inside that
container with `--network none`, read-only root, bounded tmpfs and no capabilities.
The test image is not the deployed web image. It uses a pinned official
[Playwright Python Docker image](https://playwright.dev/python/docs/docker),
which includes browser dependencies; the matching Python package is installed
only inside the test image. There is no host package installation.

Browser fixtures intercept the complete application API with synthetic identities,
profile metadata and a harmless download string. Checks exercise creation/owner
selection, assignment, rename, download, cancellation and pending revocation,
node restart confirmation/history, delivery errors, role isolation, empty/new
users, all profile states, unavailable PKI, lost creation response/idempotent
retry, pagination past 100 profiles, loading/recovery, initial session failure,
expired-session reads/mutations, keyboard dialog dismissal, browser storage,
Blob URL cleanup and logout/reload. Scenarios run at
desktop 1440px and with touch/mobile emulation at 390px and 320px; they also detect
horizontal overflow and uncaught browser exceptions.

These are frontend acceptance checks with API fixtures, not live Google OAuth,
real-node VPN routing or production deployment tests. The backend/PKI integration
checks from earlier stages remain separate. Broader security/integration checks
are covered by [stage 7 acceptance](profile-security-acceptance.md); rollout and
manual production acceptance require separate exact operator approval and stage 8
preparation. Build output and local screenshots
remain ignored/temporary. No commit, PR or publication is implied by these commands.
