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

prepare_multihop_server_pki() {
    local yc_pki="${TEST_ROOT}/yc-direct/multihop-pki"
    local aws_pki="${TEST_ROOT}/aws-direct/multihop-pki"

    mkdir -p -- \
        "${yc_pki}/multihop-ingress" \
        "${aws_pki}/transit-server" \
        "${TEST_ROOT}/aws-direct/ccd"
    cp -- "${TEST_CA_DIR}/ca.crt" "${TEST_ROOT}/crl.pem" "${yc_pki}/"
    cp -- "${TEST_CA_DIR}/ca.crt" "${TEST_ROOT}/crl.pem" "${aws_pki}/"
    cp -- \
        "${TEST_ROOT}/yc-direct/server.crt" \
        "${TEST_ROOT}/yc-direct/server.key" \
        "${TEST_ROOT}/yc-direct/tls-crypt-v2-server.key" \
        "${yc_pki}/multihop-ingress/"
    cp -- \
        "${TEST_ROOT}/aws-direct/server.crt" \
        "${TEST_ROOT}/aws-direct/server.key" \
        "${TEST_ROOT}/aws-direct/tls-crypt-v2-server.key" \
        "${aws_pki}/transit-server/"
    chmod 0755 -- \
        "${yc_pki}" "${yc_pki}/multihop-ingress" \
        "${aws_pki}" "${aws_pki}/transit-server" \
        "${TEST_ROOT}/aws-direct/ccd"
    chmod 0644 -- \
        "${yc_pki}/ca.crt" "${yc_pki}/crl.pem" \
        "${yc_pki}/multihop-ingress/"* \
        "${aws_pki}/ca.crt" "${aws_pki}/crl.pem" \
        "${aws_pki}/transit-server/"*
    cp -- "${TEST_ROOT}/aws-direct/yc-transit.ccd" \
        "${TEST_ROOT}/aws-direct/ccd/yc-transit"
    chmod 0644 -- "${TEST_ROOT}/aws-direct/ccd/yc-transit"
}

render_configs() {
    local mode="$1"
    local vpn_ipv4_cidr="$2"
    local ipv6_enabled="$3"
    local vpn_ipv6_cidr="$4"
    local multihop_enabled="$5"

    python3 - \
        "${mode}" \
        "${vpn_ipv4_cidr}" \
        "${ipv6_enabled}" \
        "${vpn_ipv6_cidr}" \
        "${multihop_enabled}" \
        "${REPOSITORY_ROOT}" \
        "${TEST_ROOT}" <<'PY'
from ipaddress import ip_network
import json
from pathlib import Path
import sys

from jinja2 import Environment, StrictUndefined

(
    mode,
    ipv4_cidr,
    ipv6_text,
    ipv6_cidr,
    multihop_text,
    repository_root,
    test_root,
) = sys.argv[1:]
ipv6_enabled = ipv6_text == "true"
multihop_enabled = multihop_text == "true"
openvpn_port = 1194
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
environment.filters["veilway_network_host"] = lambda value, offset: str(
    ip_network(value).network_address + int(offset)
)
environment.filters["to_json"] = json.dumps
values = {
    "ansible_facts": {"default_ipv4": {"interface": "eth0"}},
    "veilway_config_root": "/etc/veilway",
    "veilway_image_name": "veilway/openvpn:2.6-ubuntu24.04",
    "veilway_openvpn_port": openvpn_port,
    "veilway_operator_ipv4_cidrs": ["198.51.100.10/32"],
    "veilway_operator_ipv6_cidrs": ["2001:db8:ffff::1/128"] if ipv6_enabled else [],
    "veilway_tunnel_interface": "tun0",
    "veilway_vpc_ipv4_cidr": "10.241.2.0/24" if ipv6_enabled else "10.241.1.0/24",
    "veilway_vpc_ipv6_cidr": "2001:db8:100::/56",
    "veilway_vpn_ipv4_cidr": ipv4_cidr,
    "veilway_enable_ipv6": ipv6_enabled,
    "veilway_public_ipv6": "2001:db8:100::10",
    "veilway_vpn_ipv6_cidr": ipv6_cidr,
    "veilway_mode": mode,
    "veilway_multihop_enabled": multihop_enabled,
    "veilway_multihop_ingress_port": 1195,
    "veilway_transit_port": 1196,
    "veilway_multihop_tunnel_interface": "tun-multihop",
    "veilway_transit_tunnel_interface": "tun-transit",
    "veilway_multihop_ipv4_cidr": "10.242.30.0/24",
    "veilway_transit_ipv4_cidr": "10.242.40.0/29",
    "veilway_multihop_ipv6_cidr": "fd12:3456:789a:30::/64",
    "veilway_transit_ipv6_cidr": "fd12:3456:789a:40::/64",
    "veilway_multihop_route_table_ipv4": 242,
    "veilway_multihop_route_table_ipv6": 243,
    "veilway_multihop_route_priority": 24200,
    "veilway_yc_public_ipv4": "192.0.2.10",
    "veilway_aws_public_ipv4": "192.0.2.20",
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

if ipv6_enabled:
    values["veilway_netplan_cloud_definition"] = "eth0"
    template = environment.from_string(
        Path(template_root, "90-veilway-ipv6.yaml.j2").read_text(
            encoding="utf-8"
        )
    )
    Path(output_root, "netplan-ipv6.yaml").write_text(
        template.render(**values), encoding="utf-8"
    )

if multihop_enabled:
    extra_templates = (
        (
            ("openvpn-multihop-server.conf.j2", "multihop-server.conf"),
            ("openvpn-transit-client.conf.j2", "transit-client.conf"),
            ("transit-route-up.j2", "transit-route-up"),
            ("transit-route-down.j2", "transit-route-down"),
        )
        if mode == "yc-direct"
        else (
            ("openvpn-transit-server.conf.j2", "transit-server.conf"),
            ("yc-transit.ccd.j2", "yc-transit.ccd"),
            ("unbound-transit.conf.j2", "unbound-transit.conf"),
        )
    )
    for template_name, output_name in extra_templates:
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
    if [[ "${multihop_enabled}" == 'true' && "${mode}" == 'yc-direct' ]]; then
        chmod 0644 -- \
            "${TEST_ROOT}/${mode}/multihop-server.conf" \
            "${TEST_ROOT}/${mode}/transit-client.conf"
        chmod 0755 -- \
            "${TEST_ROOT}/${mode}/transit-route-up" \
            "${TEST_ROOT}/${mode}/transit-route-down"
    elif [[ "${multihop_enabled}" == 'true' ]]; then
        chmod 0644 -- \
            "${TEST_ROOT}/${mode}/transit-server.conf" \
            "${TEST_ROOT}/${mode}/yc-transit.ccd" \
            "${TEST_ROOT}/${mode}/unbound-transit.conf"
    fi
}

test_rendered_host_configs() {
    local mode="$1"
    local expected_port=1194

    python3 -m json.tool "${TEST_ROOT}/${mode}/daemon.json" >/dev/null
    docker compose \
        --file "${TEST_ROOT}/${mode}/compose.yaml" \
        config --quiet
    grep -Fq -- 'push "redirect-gateway def1 bypass-dhcp block-local' \
        "${TEST_ROOT}/${mode}/server.conf"
    grep -Fq -- \
        "udp dport ${expected_port} counter name openvpn_ingress_raw" \
        "${TEST_ROOT}/${mode}/nftables.conf"
    if grep -E -- \
        'openvpn_ingress_raw.*(accept|drop|reject)' \
        "${TEST_ROOT}/${mode}/nftables.conf" >/dev/null; then
        printf '%s\n' 'raw OpenVPN counter unexpectedly has a verdict' >&2
        return 1
    fi
    grep -Fq -- \
        "udp dport ${expected_port} counter name openvpn_ingress accept" \
        "${TEST_ROOT}/${mode}/nftables.conf"
    grep -Fxq -- "port ${expected_port}" \
        "${TEST_ROOT}/${mode}/server.conf"
    if [[ "${mode}" == 'aws-direct' ]]; then
        grep -Fxq -- '      dhcp6: false' \
            "${TEST_ROOT}/${mode}/netplan-ipv6.yaml"
        grep -Fxq -- '        - "2001:db8:100::10/128"' \
            "${TEST_ROOT}/${mode}/netplan-ipv6.yaml"
    fi
}

test_rendered_multihop_configs() {
    local mode="$1"

    docker compose \
        --file "${TEST_ROOT}/${mode}/compose.yaml" \
        config --quiet
    if [[ "${mode}" == 'yc-direct' ]]; then
        grep -Fxq -- 'port 1195' "${TEST_ROOT}/${mode}/multihop-server.conf"
        grep -Fxq -- 'remote 192.0.2.20 1196' "${TEST_ROOT}/${mode}/transit-client.conf"
        grep -Fq -- 'unreachable default metric 42760 table 242' \
            "${TEST_ROOT}/${mode}/transit-route-down"
        grep -Fq -- 'iifname $multihop_if oifname $transit_if' \
            "${TEST_ROOT}/${mode}/nftables.conf"
        if grep -Fq -- 'oifname $wan_if ip saddr $multihop_v4 masquerade' \
            "${TEST_ROOT}/${mode}/nftables.conf"; then
            printf '%s\n' 'Yandex multi-hop unexpectedly has WAN NAT' >&2
            return 1
        fi
    else
        grep -Fxq -- 'port 1196' "${TEST_ROOT}/${mode}/transit-server.conf"
        grep -Fxq -- 'ccd-exclusive' "${TEST_ROOT}/${mode}/transit-server.conf"
        grep -Fxq -- 'iroute 10.242.30.0 255.255.255.0' \
            "${TEST_ROOT}/${mode}/yc-transit.ccd"
        grep -Fq -- 'oifname $wan_if ip saddr $multihop_v4 masquerade' \
            "${TEST_ROOT}/${mode}/nftables.conf"
        test_unbound_file "${TEST_ROOT}/${mode}/unbound-transit.conf"
    fi
}

test_unbound_config() {
    local mode="$1"
    test_unbound_file "${TEST_ROOT}/${mode}/unbound.conf"
}

test_unbound_file() {
    local config_path="$1"
    docker run --rm \
        --interactive \
        --network none \
        --read-only \
        --cap-drop ALL \
        --security-opt no-new-privileges:true \
        --volume "${config_path}:/etc/unbound/unbound.conf:ro" \
        "${VPN_IMAGE}" \
        /usr/sbin/unbound-checkconf /etc/unbound/unbound.conf >/dev/null
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

test_multihop_openvpn_server_startup() {
    local mode="$1"
    local config_name pki_path
    local -a extra_volume=()

    if [[ "${mode}" == 'yc-direct' ]]; then
        config_name='multihop-server.conf'
        pki_path="${TEST_ROOT}/yc-direct/multihop-pki"
    else
        config_name='transit-server.conf'
        pki_path="${TEST_ROOT}/aws-direct/multihop-pki"
        extra_volume=(
            --volume
            "${TEST_ROOT}/aws-direct/ccd:/etc/openvpn/ccd:ro"
        )
    fi

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
        --volume "${TEST_ROOT}/${mode}/${config_name}:/etc/openvpn/server.conf:ro" \
        --volume "${pki_path}:/etc/veilway/pki:ro" \
        "${extra_volume[@]}" \
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

    render_configs yc-direct 10.242.10.0/24 false fd00::/64 false
    render_configs aws-direct 10.242.20.0/24 true fd12:3456:789a:20::/64 false
    chmod 0711 -- "${TEST_ROOT}"
    chmod 0755 -- "${TEST_ROOT}/yc-direct" "${TEST_ROOT}/aws-direct"

    test_unbound_config yc-direct
    test_unbound_config aws-direct
    test_rendered_host_configs yc-direct
    test_rendered_host_configs aws-direct
    test_openvpn_startup yc-direct
    test_openvpn_startup aws-direct

    render_configs yc-direct 10.242.10.0/24 false fd00::/64 true
    render_configs aws-direct 10.242.20.0/24 true fd12:3456:789a:20::/64 true
    test_rendered_multihop_configs yc-direct
    test_rendered_multihop_configs aws-direct
    prepare_multihop_server_pki
    test_multihop_openvpn_server_startup yc-direct
    test_multihop_openvpn_server_startup aws-direct

    printf '%s\n' "${PROGRAM_NAME}: container configuration smoke tests passed"
}

main "$@"
