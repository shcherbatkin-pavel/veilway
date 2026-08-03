#!/usr/bin/env bash

set -u
set -o pipefail

readonly PROGRAM_NAME="${0##*/}"
USE_SUDO=0

usage() {
    cat <<EOF
Usage: ${PROGRAM_NAME} [--sudo | --help]

Collect read-only Ubuntu network diagnostics on the local host.

  --sudo  Also run the explicitly listed read-only firewall commands with sudo.
  --help  Show this help text and exit.

The script never connects to another host. Results are written under the
Git-ignored audit-results/ directory next to this repository's scripts/ folder.
EOF
}

if (( $# > 1 )); then
    printf 'Error: expected at most one argument.\n\n' >&2
    usage >&2
    exit 2
fi

case "${1-}" in
    "")
        ;;
    --sudo)
        USE_SUDO=1
        ;;
    --help|-h)
        usage
        exit 0
        ;;
    *)
        printf 'Error: unknown argument: %s\n\n' "$1" >&2
        usage >&2
        exit 2
        ;;
esac

umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
readonly RESULTS_ROOT="${REPOSITORY_ROOT}/audit-results"

raw_hostname="$(hostname 2>/dev/null || true)"
safe_hostname="$(printf '%s' "${raw_hostname:-unknown-host}" | tr -cs 'A-Za-z0-9._-' '_' | sed 's/^_*//; s/_*$//')"
readonly SAFE_HOSTNAME="${safe_hostname:-unknown-host}"
readonly UTC_TIMESTAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
readonly OUTPUT_DIR="${RESULTS_ROOT}/${SAFE_HOSTNAME}-${UTC_TIMESTAMP}"
readonly REPORT_FILE="${OUTPUT_DIR}/report.txt"

mkdir -p -- "${RESULTS_ROOT}"
chmod 0700 -- "${RESULTS_ROOT}"
if ! mkdir -- "${OUTPUT_DIR}"; then
    printf 'Error: refusing to overwrite an existing audit directory: %s\n' "${OUTPUT_DIR}" >&2
    exit 1
fi
chmod 0700 -- "${OUTPUT_DIR}"
: > "${REPORT_FILE}"
chmod 0600 -- "${REPORT_FILE}"

print_command() {
    local argument
    printf 'Command:' >> "${REPORT_FILE}"
    for argument in "$@"; do
        printf ' %q' "${argument}" >> "${REPORT_FILE}"
    done
    printf '\n' >> "${REPORT_FILE}"
}

run_command() {
    local title="$1"
    local exit_code
    shift

    {
        printf '\n## %s\n' "${title}"
    } >> "${REPORT_FILE}"
    print_command "$@"
    printf '%s\n' 'Output:' >> "${REPORT_FILE}"

    if "$@" >> "${REPORT_FILE}" 2>&1; then
        exit_code=0
    else
        exit_code=$?
    fi
    printf 'Exit code: %d\n' "${exit_code}" >> "${REPORT_FILE}"
}

record_unavailable() {
    local title="$1"
    local command_name="$2"

    {
        printf '\n## %s\n' "${title}"
        printf 'Command unavailable: %s\n' "${command_name}"
    } >> "${REPORT_FILE}"
}

run_if_available() {
    local title="$1"
    local command_name="$2"
    shift 2

    if command -v -- "${command_name}" >/dev/null 2>&1; then
        run_command "${title}" "${command_name}" "$@"
    else
        record_unavailable "${title}" "${command_name}"
    fi
}

run_sudo_if_available() {
    local title="$1"
    local command_name="$2"
    shift 2

    if command -v -- "${command_name}" >/dev/null 2>&1; then
        run_command "${title}" sudo -- "${command_name}" "$@"
    else
        record_unavailable "${title}" "${command_name}"
    fi
}

{
    printf '# Veilway VM audit\n'
    printf 'Started (UTC): %s\n' "${UTC_TIMESTAMP}"
    printf 'Host: %s\n' "${SAFE_HOSTNAME}"
    printf 'Privileged firewall checks requested: %s\n' "$([[ ${USE_SUDO} -eq 1 ]] && printf yes || printf no)"
    printf 'Notice: this report contains sensitive infrastructure data. Do not commit or publish it.\n'
} >> "${REPORT_FILE}"

run_if_available "Kernel" uname -a
run_if_available "Ubuntu release" sed -n '1,120p' /etc/os-release
run_if_available "System uptime" uptime

run_if_available "Network links" ip -brief link show
run_if_available "Network addresses" ip -brief address show
run_if_available "IPv4 routes (all tables)" ip -4 route show table all
run_if_available "IPv6 routes (all tables)" ip -6 route show table all
run_if_available "IPv4 policy rules" ip -4 rule show
run_if_available "IPv6 policy rules" ip -6 rule show
run_if_available "IP forwarding settings" sysctl net.ipv4.ip_forward net.ipv6.conf.all.forwarding net.ipv4.conf.all.rp_filter
run_if_available "Listening TCP and UDP sockets (no process data)" ss -lntu
run_if_available "WireGuard network links (no peer or key data)" ip -details link show type wireguard

run_if_available "OpenVPN package version" dpkg-query -W '-f=${binary:Package}\t${Version}\t${db:Status-Abbrev}\n' openvpn
run_if_available "OpenVPN Access Server package version" dpkg-query -W '-f=${binary:Package}\t${Version}\t${db:Status-Abbrev}\n' openvpn-as
run_if_available "WireGuard package version" dpkg-query -W '-f=${binary:Package}\t${Version}\t${db:Status-Abbrev}\n' wireguard
run_if_available "WireGuard tools package version" dpkg-query -W '-f=${binary:Package}\t${Version}\t${db:Status-Abbrev}\n' wireguard-tools

run_if_available "Relevant loaded systemd services" systemctl list-units --no-pager --all --type=service 'openvpn*' 'openvpnas*' 'wg-quick*'
run_if_available "Relevant installed systemd unit files" systemctl list-unit-files --no-pager --type=service 'openvpn*' 'openvpnas*' 'wg-quick*'
run_if_available "OpenVPN service properties" systemctl show --no-pager --property=Id,LoadState,ActiveState,SubState,UnitFileState,FragmentPath openvpn.service
run_if_available "OpenVPN Access Server service properties" systemctl show --no-pager --property=Id,LoadState,ActiveState,SubState,UnitFileState,FragmentPath openvpnas.service
run_if_available "OpenVPN server template properties" systemctl show --no-pager --property=Id,LoadState,ActiveState,SubState,UnitFileState,FragmentPath openvpn-server@.service
run_if_available "WireGuard quick template properties" systemctl show --no-pager --property=Id,LoadState,ActiveState,SubState,UnitFileState,FragmentPath wg-quick@.service

run_if_available "UFW status (unprivileged)" ufw status verbose

if (( USE_SUDO == 1 )); then
    warning_message=$(cat <<'EOF'

WARNING: --sudo was requested.
The script is about to invoke sudo for these read-only firewall commands only:
  sudo -- ufw status verbose
  sudo -- nft list ruleset
  sudo -- iptables-save
  sudo -- ip6tables-save
The host may prompt for your sudo password. No sudo credential pre-validation is performed.
EOF
)
    printf '%s\n' "${warning_message}" | tee -a "${REPORT_FILE}" >&2

    if command -v -- sudo >/dev/null 2>&1; then
        run_sudo_if_available "UFW status (privileged)" ufw status verbose
        run_sudo_if_available "nftables ruleset (privileged)" nft list ruleset
        run_sudo_if_available "IPv4 iptables ruleset (privileged)" iptables-save
        run_sudo_if_available "IPv6 iptables ruleset (privileged)" ip6tables-save
    else
        record_unavailable "Privileged firewall checks" sudo
    fi
else
    {
        printf '\n## Privileged firewall checks\n'
        printf 'Skipped. Re-run with --sudo only after reviewing the documented command list.\n'
    } >> "${REPORT_FILE}"
fi

printf '\nAudit complete. Review locally before copying: %s\n' "${REPORT_FILE}"
