#!/usr/bin/env bash
set -euo pipefail
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
if [[ $# -ne 1 || "$1" != '--build' ]]; then
    printf '%s\n' 'Usage: scripts/test-profile-api.sh --build' >&2
    exit 2
fi
command -v -- docker >/dev/null 2>&1 || { printf '%s\n' 'Docker is required.' >&2; exit 1; }
cd -- "${REPOSITORY_ROOT}"
docker build --file web/backend/Dockerfile.test --tag veilway-control-api-test:stage4 web/backend
docker build --file web/pki/Dockerfile.integration --build-context backend=web/backend --build-context agent=deploy/roles/veilway_crl_agent/files \
    --tag veilway-profile-integration-test:stage5 web/pki
docker run --rm --pull never --network none --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=64m --cap-drop ALL \
    --security-opt no-new-privileges:true veilway-profile-integration-test:stage5
printf '%s\n' 'PostgreSQL concurrency/migration tests require a separate disposable VEILWAY_TEST_POSTGRES_URL.'
