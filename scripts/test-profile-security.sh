#!/usr/bin/env bash
# Full backend acceptance against a disposable PostgreSQL and synthetic PKI.
set -euo pipefail
umask 077
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
if [[ $# -ne 1 || "$1" != '--build' ]]; then
    printf '%s\n' 'Usage: scripts/test-profile-security.sh --build' >&2
    exit 2
fi
command -v -- docker >/dev/null 2>&1 || { printf '%s\n' 'Docker is required.' >&2; exit 1; }
cd -- "${REPOSITORY_ROOT}"
docker build --file web/backend/Dockerfile.test --tag veilway-control-api-test:stage4 web/backend
docker build --file web/pki/Dockerfile.integration --build-context backend=web/backend \
    --build-context agent=deploy/roles/veilway_crl_agent/files \
    --tag veilway-profile-integration-test:stage7 web/pki
readonly POSTGRES_CONTAINER="veilway-security-postgres-$$-${RANDOM}"
postgres_started=false
cleanup() {
    if [[ "${postgres_started}" == true ]]; then
        docker stop --time 5 "${POSTGRES_CONTAINER}" >/dev/null
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# No host mounts, published ports or external networking. The password is a
# public synthetic fixture; all database data exists only in temporary RAM.
docker run --detach --rm --pull never --name "${POSTGRES_CONTAINER}" \
    --network none --read-only --security-opt no-new-privileges:true \
    --tmpfs /var/lib/postgresql/data:rw,noexec,nosuid,size=256m \
    --tmpfs /var/run/postgresql:rw,noexec,nosuid,size=8m \
    --tmpfs /tmp:rw,noexec,nosuid,size=16m \
    --env POSTGRES_PASSWORD=synthetic-stage7-test-only \
    --env POSTGRES_DB=veilway_test postgres:17.4-alpine >/dev/null
postgres_started=true
ready=false
for attempt in {1..30}; do
    if docker exec "${POSTGRES_CONTAINER}" pg_isready --username postgres --dbname veilway_test >/dev/null 2>&1; then
        ready=true
        break
    fi
    sleep 1
done
[[ "${ready}" == true ]] || { printf '%s\n' 'Disposable PostgreSQL did not become ready.' >&2; exit 1; }
docker run --rm --pull never --network "container:${POSTGRES_CONTAINER}" --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=96m --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --env VEILWAY_TEST_POSTGRES_URL=postgresql+psycopg://postgres:synthetic-stage7-test-only@127.0.0.1:5432/veilway_test \
    veilway-profile-integration-test:stage7
