#!/usr/bin/env bash
set -euo pipefail
umask 077
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ $# -ne 1 || "$1" != '--build' ]]; then
    printf 'Usage: %s --build\n' "${0##*/}" >&2
    exit 2
fi
command -v -- python3 >/dev/null 2>&1 || {
    printf '%s\n' 'Required command unavailable: python3' >&2
    exit 1
}
exec python3 "${SCRIPT_DIR}/run-tests.py" all --build
