#!/usr/bin/env bash
# Synthetic TLS test across two mounts of the same disposable directory.
set -euo pipefail
umask 077
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
if [[ $# -ne 1 || "$1" != '--build' ]]; then
    printf '%s\n' 'Usage: scripts/test-crl-mount.sh --build' >&2
    exit 2
fi
command -v -- docker >/dev/null 2>&1 || { printf '%s\n' 'Docker is required.' >&2; exit 1; }
cd -- "${REPOSITORY_ROOT}"
docker build --file web/backend/Dockerfile.test --tag veilway-control-api-test:stage4 web/backend
docker build --file web/pki/Dockerfile.integration --build-context backend=web/backend \
    --build-context agent=deploy/roles/veilway_crl_agent/files --tag veilway-profile-integration-test:stage5 web/pki
readonly CRL_TEST_DIRECTORY="$(mktemp -d /tmp/veilway-crl-mount.XXXXXXXX)"
cleanup() {
    rm -f -- "${CRL_TEST_DIRECTORY}/crl.pem"
    rmdir -- "${CRL_TEST_DIRECTORY}"
}
trap cleanup EXIT
docker run --rm --pull never --network none --read-only \
    --user "$(id -u):$(id -g)" --tmpfs /tmp:rw,noexec,nosuid,size=96m \
    --cap-drop ALL --security-opt no-new-privileges:true \
    --mount "type=bind,src=${CRL_TEST_DIRECTORY},dst=/test-crl-write" \
    --mount "type=bind,src=${CRL_TEST_DIRECTORY},dst=/test-crl-read,readonly" \
    --env VEILWAY_TEST_CRL_WRITER=/test-crl-write --env VEILWAY_TEST_CRL_READER=/test-crl-read \
    veilway-profile-integration-test:stage5 pytest -q -p no:cacheprovider tests/test_crl_openvpn.py
