#!/usr/bin/env bash

set -euo pipefail

umask 077

readonly PROGRAM_NAME="${0##*/}"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"
readonly OPENSSL_CONFIG="${REPOSITORY_ROOT}/pki/openssl-ca.cnf"
readonly VPN_IMAGE="veilway/openvpn:2.6-ubuntu24.04"
readonly TEST_ROOT="$(mktemp -d -- /tmp/veilway-container-smoke.XXXXXX)"
readonly TEST_CA_DIR="${TEST_ROOT}/ca"

cleanup() {
    case "${TEST_ROOT}" in
        /tmp/veilway-container-smoke.*)
            rm -rf -- "${TEST_ROOT}"
            ;;
        *)
            printf 'Error: refusing unexpected cleanup path: %s\n' "${TEST_ROOT}" >&2
            return 1
            ;;
    esac
}

trap cleanup EXIT

require_command() {
    local command_name="$1"
    command -v -- "${command_name}" >/dev/null 2>&1 || {
        printf 'Error: required command is unavailable: %s\n' "${command_name}" >&2
        exit 1
    }
}

test_image_contents() {
    docker run --rm \
        --network none \
        --read-only \
        --cap-drop ALL \
        --security-opt no-new-privileges:true \
        "${VPN_IMAGE}" \
        /bin/bash -ceu '
            openvpn --version | grep -Eq -- "^OpenVPN 2[.]6[.]"
            unbound -V | grep -Eq -- "^Version 1[.]19[.]"
            test "$(id -u openvpn)" = 900
            test "$(id -g openvpn)" = 900
            test -r /usr/share/dns/root.key
            test -x /usr/local/bin/wait-for-interface
        '
}

initialize_synthetic_ca() {
    mkdir -p -- "${TEST_CA_DIR}/private" "${TEST_CA_DIR}/newcerts"
    : > "${TEST_CA_DIR}/index.txt"
    printf '1000\n' > "${TEST_CA_DIR}/serial"
    printf '1000\n' > "${TEST_CA_DIR}/crlnumber"

    openssl genpkey \
        -algorithm EC \
        -pkeyopt ec_paramgen_curve:P-256 \
        -out "${TEST_CA_DIR}/private/ca.key" >/dev/null 2>&1
    openssl req \
        -config "${OPENSSL_CONFIG}" \
        -new \
        -x509 \
        -key "${TEST_CA_DIR}/private/ca.key" \
        -sha256 \
        -days 1 \
        -extensions v3_ca \
        -out "${TEST_CA_DIR}/ca.crt" >/dev/null 2>&1
}

create_synthetic_server() {
    local mode="$1"
    local endpoint_dir="${TEST_ROOT}/${mode}"

    mkdir -- "${endpoint_dir}"
    openssl genpkey \
        -algorithm EC \
        -pkeyopt ec_paramgen_curve:P-256 \
        -out "${endpoint_dir}/server.key" >/dev/null 2>&1
    openssl req \
        -new \
        -sha256 \
        -key "${endpoint_dir}/server.key" \
        -subj "/CN=${mode}" \
        -out "${endpoint_dir}/server.csr" >/dev/null 2>&1
    (
        cd -- "${TEST_CA_DIR}"
        openssl ca \
            -batch \
            -config "${OPENSSL_CONFIG}" \
            -extensions server_cert \
            -days 1 \
            -notext \
            -in "${endpoint_dir}/server.csr" \
            -out "${endpoint_dir}/server.crt" >/dev/null 2>&1
    )
    cp -- "${TEST_CA_DIR}/ca.crt" "${endpoint_dir}/ca.crt"

    docker run --rm \
        --network none \
        --read-only \
        --cap-drop ALL \
        --security-opt no-new-privileges:true \
        --user "$(id -u):$(id -g)" \
        --volume "${endpoint_dir}:/work" \
        --workdir /work \
        "${VPN_IMAGE}" \
        /usr/sbin/openvpn \
        --genkey tls-crypt-v2-server /work/tls-crypt-v2-server.key >/dev/null
    docker run --rm \
        --network none \
        --read-only \
        --cap-drop ALL \
        --security-opt no-new-privileges:true \
        --user "$(id -u):$(id -g)" \
        --volume "${endpoint_dir}:/work" \
        --workdir /work \
        "${VPN_IMAGE}" \
        /usr/sbin/openvpn \
        --tls-crypt-v2 /work/tls-crypt-v2-server.key \
        --genkey tls-crypt-v2-client /work/tls-crypt-v2-client.test.key >/dev/null
    grep -Fq -- 'BEGIN OpenVPN tls-crypt-v2 client key' \
        "${endpoint_dir}/tls-crypt-v2-client.test.key"
    rm -- "${endpoint_dir}/tls-crypt-v2-client.test.key"
}

generate_test_crl() {
    (
        cd -- "${TEST_CA_DIR}"
        openssl ca \
            -config "${OPENSSL_CONFIG}" \
            -gencrl \
            -out "${TEST_ROOT}/crl.pem" >/dev/null 2>&1
    )
    cp -- "${TEST_ROOT}/crl.pem" "${TEST_ROOT}/yc-direct/crl.pem"
    cp -- "${TEST_ROOT}/crl.pem" "${TEST_ROOT}/aws-direct/crl.pem"
}

render_configs() {
    local mode="$1"
    local vpn_ipv4_cidr="$2"
    local ipv6_enabled="$3"
    local vpn_ipv6_cidr="$4"

    python3 - \
        "${mode}" \
        "${vpn_ipv4_cidr}" \
        "${ipv6_enabled}" \
        "${vpn_ipv6_cidr}" \
        "${REPOSITORY_ROOT}" \
        "${TEST_ROOT}" <<'PY'
from ipaddress import ip_network
from pathlib import Path
import sys

from jinja2 import Environment, StrictUndefined

mode, ipv4_cidr, ipv6_text, ipv6_cidr, repository_root, test_root = sys.argv[1:]
ipv6_enabled = ipv6_text == "true"
environment = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
environment.filters["veilway_network_address"] = lambda value: str(
    ip_network(value).network_address
)
environment.filters["veilway_network_netmask"] = lambda value: str(
    ip_network(value).netmask
)
environment.filters["veilway_network_first_host"] = lambda value: str(
    next(ip_network(value).hosts())
)
values = {
    "ansible_facts": {"default_ipv4": {"interface": "eth0"}},
    "veilway_config_root": "/etc/veilway",
    "veilway_image_name": "veilway/openvpn:2.6-ubuntu24.04",
    "veilway_openvpn_port": 1194,
    "veilway_operator_ipv4_cidrs": ["198.51.100.10/32"],
    "veilway_operator_ipv6_cidrs": ["2001:db8:ffff::1/128"] if ipv6_enabled else [],
    "veilway_tunnel_interface": "tun0",
    "veilway_vpc_ipv4_cidr": "10.241.2.0/24" if ipv6_enabled else "10.241.1.0/24",
    "veilway_vpc_ipv6_cidr": "2001:db8:100::/56",
    "veilway_vpn_ipv4_cidr": ipv4_cidr,
    "veilway_enable_ipv6": ipv6_enabled,
    "veilway_vpn_ipv6_cidr": ipv6_cidr,
}
template_root = Path(repository_root, "deploy/roles/veilway_direct/templates")
output_root = Path(test_root, mode)
for template_name, output_name in (
    ("openvpn-server.conf.j2", "server.conf"),
    ("unbound.conf.j2", "unbound.conf"),
    ("compose.yaml.j2", "compose.yaml"),
    ("nftables.conf.j2", "nftables.conf"),
    ("90-veilway.conf.j2", "sysctl.conf"),
    ("docker-daemon.json.j2", "daemon.json"),
):
    template = environment.from_string(
        Path(template_root, template_name).read_text(encoding="utf-8")
    )
    Path(output_root, output_name).write_text(
        template.render(**values), encoding="utf-8"
    )
PY
    chmod 0644 -- \
        "${TEST_ROOT}/${mode}/server.conf" \
        "${TEST_ROOT}/${mode}/unbound.conf" \
        "${TEST_ROOT}/${mode}/compose.yaml" \
        "${TEST_ROOT}/${mode}/nftables.conf" \
        "${TEST_ROOT}/${mode}/sysctl.conf" \
        "${TEST_ROOT}/${mode}/daemon.json"
}

test_rendered_host_configs() {
    local mode="$1"

    python3 -m json.tool "${TEST_ROOT}/${mode}/daemon.json" >/dev/null
    docker compose \
        --file "${TEST_ROOT}/${mode}/compose.yaml" \
        config --quiet
    grep -Fq -- 'push "redirect-gateway def1 bypass-dhcp block-local' \
        "${TEST_ROOT}/${mode}/server.conf"
    grep -Fq -- \
        'udp dport 1194 counter name openvpn_ingress_raw' \
        "${TEST_ROOT}/${mode}/nftables.conf"
    if grep -E -- \
        'openvpn_ingress_raw.*(accept|drop|reject)' \
        "${TEST_ROOT}/${mode}/nftables.conf" >/dev/null; then
        printf '%s\n' 'raw OpenVPN counter unexpectedly has a verdict' >&2
        return 1
    fi
    grep -Fq -- \
        'udp dport 1194 counter name openvpn_ingress accept' \
        "${TEST_ROOT}/${mode}/nftables.conf"
}

test_unbound_config() {
    local mode="$1"
    docker run --rm \
        --interactive \
        --network none \
        --read-only \
        --cap-drop ALL \
        --security-opt no-new-privileges:true \
        --volume "${TEST_ROOT}/${mode}/unbound.conf:/test/unbound.conf:ro" \
        "${VPN_IMAGE}" \
        /usr/sbin/unbound-checkconf /test/unbound.conf >/dev/null
}

test_openvpn_startup() {
    local mode="$1"
    local endpoint_dir="${TEST_ROOT}/${mode}"

    chmod 0755 -- "${endpoint_dir}"
    chmod 0644 -- "${endpoint_dir}"/*
    docker run --rm \
        --network none \
        --read-only \
        --cap-drop ALL \
        --cap-add KILL \
        --cap-add NET_ADMIN \
        --cap-add SETGID \
        --cap-add SETUID \
        --device /dev/net/tun \
        --security-opt no-new-privileges:true \
        --tmpfs /run/openvpn:mode=0750,uid=900,gid=900 \
        --tmpfs /tmp:mode=1777 \
        --volume "${endpoint_dir}/server.conf:/etc/openvpn/server.conf:ro" \
        --volume "${endpoint_dir}:/etc/veilway/pki:ro" \
        "${VPN_IMAGE}" \
        /bin/bash -ceu '
            openvpn --config /etc/openvpn/server.conf > /tmp/test.log 2>&1 &
            process_id=$!
            for attempt in {1..40}; do
                if grep -Fq -- "Initialization Sequence Completed" /tmp/test.log; then
                    kill -TERM "${process_id}"
                    wait "${process_id}" || true
                    exit 0
                fi
                if ! kill -0 "${process_id}" 2>/dev/null; then
                    cat /tmp/test.log >&2
                    wait "${process_id}"
                fi
                sleep 0.25
            done
            cat /tmp/test.log >&2
            kill -TERM "${process_id}" 2>/dev/null || true
            wait "${process_id}" || true
            exit 1
        '
}

main() {
    require_command docker
    require_command openssl
    require_command python3
    docker image inspect "${VPN_IMAGE}" >/dev/null 2>&1 || {
        printf 'Error: build %s before running this test.\n' "${VPN_IMAGE}" >&2
        exit 1
    }

    test_image_contents
    initialize_synthetic_ca
    create_synthetic_server yc-direct
    create_synthetic_server aws-direct
    generate_test_crl

    render_configs yc-direct 10.242.10.0/24 false fd00::/64
    render_configs aws-direct 10.242.20.0/24 true fd12:3456:789a:20::/64

    test_unbound_config yc-direct
    test_unbound_config aws-direct
    test_rendered_host_configs yc-direct
    test_rendered_host_configs aws-direct
    test_openvpn_startup yc-direct
    test_openvpn_startup aws-direct

    printf '%s\n' "${PROGRAM_NAME}: container configuration smoke tests passed"
}

main "$@"
