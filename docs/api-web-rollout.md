# Point rollout of API/web for the refactoring release

This procedure prepares and installs only the API and web images from merged
release `64db1d51d38b7af215d75a3a85bc8dcf55b66bc6` (PR #9).
The expected installed source baseline is
`797058c7e36e45005e8a1a2fcbc2bc976c6f6233` (PR #8). This is an assumption
checked against public installed build inputs before any container is changed.
A different installation must receive a separately reviewed baseline/package.

The preparation tools never use SSH or contact a VM. Host access, package
transfer and every mutation below require explicit operator approval. This
procedure does not run the full Ansible web role: that role also stops PKI,
installs configuration and runs migrations. No new migration, credential sync,
PKI image/storage change, agent deployment, VM reboot or VPN configuration
change belongs to this release.

## Prepared local package

```sh
python3 scripts/prepare-api-web-release.py \
  --revision 64db1d5 --base-revision 797058c
python3 scripts/prepare-api-web-release.py \
  --revision 64db1d5 --base-revision 797058c \
  --output release-artifacts/api-web-64db1d51d38b-r4 --build
```

Without `--build`, validation performs no Docker operation and writes no
package. With the flag it builds/exports only API/web on the local Unix Docker
socket. Dependencies/base images may be downloaded during this explicit build.
Public context files/directories retain readable 0644/0755 permissions inside
the protected release directory. Builds bypass cached directory metadata, and
runtime readability is checked as UID 10001 before export.
The source contexts contain only allowlisted committed Git files; working-tree
changes, local environment files, inventories and generated PKI are excluded.
Changes to dependency manifests, migrations, entrypoints, Dockerfiles, Caddy
configuration or the Compose manifest reject this narrow deployment path.

The protected, Git-ignored directory
`release-artifacts/api-web-64db1d51d38b-r4/` contains:

- `images.tar`: both candidate images with revision-specific tags.
- `release.json`: revisions, image IDs, archive/checker checksums and expected
  baseline file checksums.
- `compose.override.json`: only API/web image references.
- `inspect-api-web-release.py`: local read-only preflight and verification.
- `build/`: exported public source contexts; these are not installed on the host.

Transfer the four package files outside `build/` only after approval, over the
existing verified transport. Keep the destination directory mode `0700` and
files `0600`; keep rollback metadata private and outside Git. Verify the
transferred `release.json` checksum against the local trusted copy before
running the transferred checker. Checksums detect corruption, not an attacker
replacing both metadata and artifacts.

## Reviewed host operations

Run these commands only on the dedicated, explicitly approved control-plane
host, with the existing local Docker permissions. There is no automatic sudo,
remote Docker context or connection to VPN nodes. Set these paths to the reviewed
installation and protected transfer directory:

```sh
umask 077
APP_ROOT=/opt/veilway-control/app
RELEASE_DIR=/opt/veilway-control/releases/api-web-64db1d51d38b-r4

python3 "$RELEASE_DIR/inspect-api-web-release.py" \
  --release-dir "$RELEASE_DIR" --app-root "$APP_ROOT"
```

Preflight compares installed public build inputs with the expected baseline,
checks package integrity and the image-only override, and verifies that all four
existing services are running. It reads only explicitly selected container
identity/state fields, never container environments, commands, logs, secret
files or CA storage. Failure prints a fixed message and changes no container.
Resolve a mismatch through reviewed preparation; do not bypass the checker.

Before cutover, confirm in the panel that no restart job is queued, dispatching
or waiting and no ambiguous restart needs review. Avoid concurrent administrative
mutations during maintenance. Keep the existing coherent backup and previous
images available; do not run image pruning. Expected downtime is limited to
the panel/API; graceful shutdown can wait for an in-flight PKI call.

The approved mutation sequence is: load the two candidate images, save old
API/web image references, stop only API/web with a 180-second grace period, then
recreate only API/web using the candidate override. The image check also rejects
a candidate whose OS/architecture differs from the running API/web image:

```sh
docker --host unix:///var/run/docker.sock image load --input "$RELEASE_DIR/images.tar"
python3 "$RELEASE_DIR/inspect-api-web-release.py" \
  --release-dir "$RELEASE_DIR" --app-root "$APP_ROOT" --require-images \
  --write-rollback "$RELEASE_DIR/rollback.override.json"

docker --host unix:///var/run/docker.sock compose --project-directory "$APP_ROOT" \
  --file "$APP_ROOT/compose.yaml" stop --timeout 180 api web
docker --host unix:///var/run/docker.sock compose --project-directory "$APP_ROOT" \
  --file "$APP_ROOT/compose.yaml" --file "$RELEASE_DIR/compose.override.json" \
  up --detach --no-deps --no-build --pull never --force-recreate api web

python3 "$RELEASE_DIR/inspect-api-web-release.py" \
  --release-dir "$RELEASE_DIR" --app-root "$APP_ROOT" --require-images \
  --verify-preserved "$RELEASE_DIR/rollback.override.json" --verify-candidate
curl --fail --silent --show-error --max-time 5 --retry 10 --retry-delay 2 \
  --retry-max-time 60 https://veilway.ru/api/readyz
```

Do not continue after a failed command. Rollback references are saved exclusively
and cannot overwrite an earlier snapshot. Postflight verifies the exact running
candidate image IDs and unchanged DB/PKI container IDs, images, start times and
restart counts. Readiness must return `{"status":"ready"}`; it checks worker
lifetimes rather than database/cloud/PKI availability.

Manually verify Google sign-in, ADMIN profile filters/metadata, USER owner-scoped
profile metadata and the absence of administrative navigation for USER. Inspect
the existing CRL delivery view. Do not create/revoke profiles or reboot nodes
as part of this smoke check. If readiness or functional checks fail, use the
approved rollback below rather than retrying jobs or changing PKI.

Keep the immutable release override and record it as the active image selection.
Future Compose operations on API/web must include that override; using the base
manifest alone can revert to its old `0.1.0` tags. Installed source directories,
the base manifest and runtime interpolation/secrets are intentionally retained.
A later full/source deployment must reconcile the active release explicitly.

## Rollback

Approval must include this recovery operation: stop/recreate only API/web using
the saved exact image IDs. Do not restore database or CA contents.

```sh
docker --host unix:///var/run/docker.sock compose --project-directory "$APP_ROOT" \
  --file "$APP_ROOT/compose.yaml" stop --timeout 180 api web
docker --host unix:///var/run/docker.sock compose --project-directory "$APP_ROOT" \
  --file "$APP_ROOT/compose.yaml" --file "$RELEASE_DIR/rollback.override.json" \
  up --detach --no-deps --no-build --pull never --force-recreate api web
python3 "$RELEASE_DIR/inspect-api-web-release.py" \
  --release-dir "$RELEASE_DIR" --app-root "$APP_ROOT" \
  --verify-preserved "$RELEASE_DIR/rollback.override.json" --verify-rollback
curl --fail --silent --show-error --max-time 5 https://veilway.ru/api/healthz
```

The old API may not implement readiness; liveness must return
`{"status":"ok"}`. Repeat sign-in and metadata checks. Retain the rollback
override as active after recovery. An ambiguous reboot dispatch requires
separate operator review; deployment never authorizes sending it again.

## Local validation

```sh
python3 scripts/test-api-web-release.py
./scripts/check.sh
```

Offline tests use synthetic files and fake Docker responses to verify image
identity, protected rollback files, unchanged DB/PKI state, allowed read-only
commands and rejection of incompatible/corrupt packages. Local Compose contract
validation checks the candidate override preserves the existing exposure,
networks, mounts and secret boundaries. These checks do not prove a production
baseline or live Google sign-in: host preflight and
manual acceptance remain required.
