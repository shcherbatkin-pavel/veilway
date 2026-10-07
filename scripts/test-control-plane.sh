#!/usr/bin/env bash

set -euo pipefail

readonly PROGRAM_NAME="${0##*/}"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
readonly FRONTEND_TEST_IMAGE='veilway-control-frontend-test:0.1.0'

usage() {
    printf 'Usage: %s --build\n' "${PROGRAM_NAME}" >&2
}

if [[ $# -ne 1 || "$1" != '--build' ]]; then
    usage
    exit 2
fi

for command_name in docker python3; do
    command -v -- "${command_name}" >/dev/null 2>&1 \
        || { printf 'Required command is unavailable: %s\n' "${command_name}" >&2; exit 1; }
done

cd -- "${REPOSITORY_ROOT}"

printf '%s\n' '[1/3] Full backend, agent, PKI and disposable PostgreSQL acceptance'
./scripts/test-profile-security.sh --build

printf '%s\n' '[2/3] Build the pinned frontend test stage and validate Compose'
docker build \
    --file web/frontend/Dockerfile \
    --target build \
    --tag "${FRONTEND_TEST_IMAGE}" \
    web/frontend
docker compose --file web/compose.yaml config --format json \
    | python3 scripts/validate-control-compose.py

printf '%s\n' '[3/3] Exercise migrations and VM sync with disposable PostgreSQL'
python3 scripts/control-postgres-smoke.py

printf '%s\n' "${PROGRAM_NAME}: all control-plane tests passed"
printf '%s\n' 'PKI lifecycle and container isolation: scripts/test-pki-service.sh --build'
