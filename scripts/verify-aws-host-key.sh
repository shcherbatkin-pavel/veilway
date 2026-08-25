#!/usr/bin/env bash

set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
readonly ENDPOINT_CONFIG="${REPOSITORY_ROOT}/operator-config/endpoints.conf"
readonly KNOWN_HOSTS_FILE="${REPOSITORY_ROOT}/operator-config/known_hosts.aws-direct"
readonly AWS_PROFILE_NAME="${AWS_PROFILE:-veilway-terraform}"
readonly AWS_REGION_NAME="eu-west-2"
readonly INSTANCE_NAME="veilway-aws-direct"

umask 077

fail() {
    printf 'verify-aws-host-key: %s\n' "$*" >&2
    exit 1
}

for command_name in aws awk git install mktemp rmdir rm ssh-keygen ssh-keyscan; do
    command -v -- "${command_name}" >/dev/null 2>&1 \
        || fail "required command not found: ${command_name}"
done

[[ -f "${ENDPOINT_CONFIG}" && ! -L "${ENDPOINT_CONFIG}" ]] \
    || fail "missing regular endpoint file: ${ENDPOINT_CONFIG}"

aws_endpoint=""
while IFS='=' read -r key value; do
    case "${key}" in
        aws_direct_endpoint)
            [[ -z "${aws_endpoint}" ]] \
                || fail "duplicate aws_direct_endpoint entry"
            aws_endpoint="${value}"
            ;;
    esac
done < "${ENDPOINT_CONFIG}"

[[ "${aws_endpoint}" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] \
    || fail "aws_direct_endpoint is not a plain IPv4 address"
IFS=. read -r -a endpoint_octets <<< "${aws_endpoint}"
for endpoint_octet in "${endpoint_octets[@]}"; do
    [[ "${endpoint_octet}" == "0" || "${endpoint_octet}" != 0* ]] \
        || fail "aws_direct_endpoint contains a leading-zero octet"
    ((10#${endpoint_octet} <= 255)) \
        || fail "aws_direct_endpoint contains an invalid octet"
done

instance_id="$(
    aws ec2 describe-instances \
        --profile "${AWS_PROFILE_NAME}" \
        --region "${AWS_REGION_NAME}" \
        --filters \
            "Name=tag:Name,Values=${INSTANCE_NAME}" \
            Name=instance-state-name,Values=pending,running,stopping,stopped \
        --query 'Reservations[].Instances[].InstanceId' \
        --output text
)"
[[ "${instance_id}" =~ ^i-[0-9a-f]+$ ]] \
    || fail "expected exactly one non-terminated aws-direct instance"

console_fingerprint="$(
    aws ec2 get-console-output \
        --profile "${AWS_PROFILE_NAME}" \
        --region "${AWS_REGION_NAME}" \
        --instance-id "${instance_id}" \
        --latest \
        --query Output \
        --output text \
        | awk '/\(ED25519\)/ {
            for (field = 1; field <= NF; field++) {
                if ($field ~ /^SHA256:/) {
                    print $field
                }
            }
        }'
)"
[[ "${console_fingerprint}" == SHA256:* && "${console_fingerprint}" != *$'\n'* ]] \
    || fail "console output does not contain exactly one ED25519 fingerprint"

temporary_directory="$(mktemp -d --tmpdir veilway-aws-host-key.XXXXXXXXXX)"
temporary_key_file="${temporary_directory}/known_hosts"
cleanup() {
    rm -f -- "${temporary_key_file}"
    rmdir -- "${temporary_directory}" 2>/dev/null || true
}
trap cleanup EXIT

ssh-keyscan -4 -T 10 -t ed25519 "${aws_endpoint}" \
    > "${temporary_key_file}" 2>/dev/null \
    || fail "ssh-keyscan could not retrieve the ED25519 host key"
[[ -s "${temporary_key_file}" ]] \
    || fail "ssh-keyscan returned an empty ED25519 host key"

scanned_fingerprint="$(
    ssh-keygen -E sha256 -lf "${temporary_key_file}" \
        | awk 'NR == 1 { print $2 }'
)"
[[ "${scanned_fingerprint}" == "${console_fingerprint}" ]] \
    || fail "ED25519 fingerprint mismatch; refusing to trust the SSH key"

git check-ignore --quiet --no-index -- "${KNOWN_HOSTS_FILE}" \
    || fail "known_hosts output is not ignored by Git"
install -m 0600 -- "${temporary_key_file}" "${KNOWN_HOSTS_FILE}"

printf '%s\n' 'AWS SSH ED25519 host key: verified against EC2 console output'
printf '%s\n' 'operator-config/known_hosts.aws-direct: protected and ignored'
