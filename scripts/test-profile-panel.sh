#!/usr/bin/env bash
set -euo pipefail
umask 077
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
if [[ $# -ne 1 || "$1" != '--build' ]]; then
    printf '%s\n' 'Usage: scripts/test-profile-panel.sh --build' >&2
    exit 2
fi
command -v -- docker >/dev/null 2>&1 || { printf '%s\n' 'Docker is required.' >&2; exit 1; }
cd -- "${REPOSITORY_ROOT}"
docker build --file web/frontend/Dockerfile.browser --tag veilway-profile-panel-test:stage6 web/frontend
docker run --rm --init --pull never --network none --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=256m --shm-size 256m --cap-drop ALL \
    --security-opt no-new-privileges:true veilway-profile-panel-test:stage6
