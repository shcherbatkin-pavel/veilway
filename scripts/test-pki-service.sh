#!/usr/bin/env bash
set -euo pipefail
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
if [[ $# -ne 1 || "$1" != '--build' ]]; then
    printf '%s\n' 'Usage: scripts/test-pki-service.sh --build' >&2
    exit 2
fi
for command_name in docker; do
    command -v -- "${command_name}" >/dev/null 2>&1 || {
        printf 'Required command unavailable: %s\n' "${command_name}" >&2
        exit 1
    }
done
cd -- "${REPOSITORY_ROOT}"
docker build --tag veilway-control-pki:0.1.0 web/pki
docker build --file web/pki/Dockerfile.test --tag veilway-control-pki-test:0.1.0 web/pki
docker run --rm --pull never --network none --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=64m,mode=1777 \
    --tmpfs /var/lib/veilway-pki:rw,noexec,nosuid,size=64m,mode=0700 \
    --tmpfs /run/veilway-pki:rw,noexec,nosuid,size=1m,mode=0700 \
    --tmpfs /run/secrets:rw,noexec,nosuid,size=1m,mode=0700 \
    --cap-drop ALL --cap-add CHOWN --cap-add SETUID --cap-add SETGID \
    --security-opt no-new-privileges:true veilway-control-pki-test:0.1.0
# Production PKI never has CHOWN. The root test orchestrator uses it only to
# provision synthetic fixture files owned by separate service identities.
