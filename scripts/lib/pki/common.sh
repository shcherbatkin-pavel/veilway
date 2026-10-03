#!/usr/bin/env bash
# Definitions only; loaded by scripts/veilway-pki.

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

require_command() {
    local command_name="$1"
    command -v -- "${command_name}" >/dev/null 2>&1 || fail "required command is unavailable: ${command_name}"
}

ensure_directory() {
    local directory_path="$1"
    local directory_mode="$2"

    [[ ! -L "${directory_path}" ]] || fail "refusing symlinked directory: ${directory_path}"
    mkdir -p -- "${directory_path}"
    chmod "${directory_mode}" -- "${directory_path}"
}

require_regular_file() {
    local file_path="$1"
    [[ -f "${file_path}" && ! -L "${file_path}" ]] || fail "required regular file is missing: ${file_path}"
}

validate_private_roots() {
    local directory_path
    for directory_path in "${SECRETS_ROOT}" "${PKI_ROOT}" "${CA_DIR}" "${CLIENTS_DIR}" "${ENDPOINTS_DIR}" "${PROFILES_DIR}"; do
        [[ ! -L "${directory_path}" ]] || fail "refusing symlinked private storage path: ${directory_path}"
    done
}

validate_mode() {
    case "$1" in
        yc-direct|aws-direct|yc-aws-multihop)
            ;;
        *)
            fail "mode must be yc-direct, aws-direct, or yc-aws-multihop"
            ;;
    esac
}

validate_server_mode() {
    case "$1" in
        yc-direct|aws-direct|yc-multihop-ingress|aws-transit)
            ;;
        *)
            fail "server mode must be yc-direct, aws-direct, yc-multihop-ingress, or aws-transit"
            ;;
    esac
}

validate_device() {
    local device_id="$1"

    [[ -n "${device_id}" && "${#device_id}" -le 48 ]] \
        || fail "device identifier must contain 1 to 48 characters"
    case "${device_id}" in
        -*|*-|*--*|*[!abcdefghijklmnopqrstuvwxyz0123456789-]*)
            fail "device identifier must use lowercase ASCII letters, digits, and single internal hyphens"
            ;;
    esac
}

port_for_mode() {
    validate_mode "$1"
    case "$1" in
        yc-direct)
            printf '%s\n' '1194'
            ;;
        aws-direct)
            printf '%s\n' '1194'
            ;;
        yc-aws-multihop)
            printf '%s\n' '1195'
            ;;
    esac
}

server_directory_for_mode() {
    validate_mode "$1"
    case "$1" in
        yc-direct|aws-direct)
            printf '%s\n' "$1"
            ;;
        yc-aws-multihop)
            printf '%s\n' 'yc-multihop-ingress'
            ;;
    esac
}

server_identity_for_mode() {
    server_directory_for_mode "$1"
}

validate_profile_name() {
    local profile_name="$1"
    local device_id

    case "${profile_name}" in
        *-yc-aws-multihop)
            device_id="${profile_name%-yc-aws-multihop}"
            ;;
        *-yc-direct)
            device_id="${profile_name%-yc-direct}"
            ;;
        *-aws-direct)
            device_id="${profile_name%-aws-direct}"
            ;;
        *)
            fail "unknown Veilway profile name: ${profile_name}"
            ;;
    esac
    validate_device "${device_id}"
}

validate_ipv4() {
    local value="$1"
    local first second third fourth extra octet

    [[ "${value}" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]] || return 1
    IFS=. read -r first second third fourth extra <<< "${value}"
    [[ -z "${extra:-}" ]] || return 1
    for octet in "${first}" "${second}" "${third}" "${fourth}"; do
        [[ "${octet}" == "0" || "${octet}" != 0* ]] || return 1
        (( 10#${octet} <= 255 )) || return 1
    done
}

read_endpoint() {
    local mode="$1"
    local wanted_key key value selected=""

    require_regular_file "${ENDPOINT_CONFIG}"
    case "${mode}" in
        yc-direct|yc-aws-multihop)
            wanted_key="yc_direct_endpoint"
            ;;
        aws-direct)
            wanted_key="aws_direct_endpoint"
            ;;
    esac

    while IFS='=' read -r key value; do
        case "${key}" in
            ""|'#'*)
                continue
                ;;
        esac
        [[ "${key}" == "${wanted_key}" ]] || continue
        [[ -z "${selected}" ]] || fail "duplicate ${wanted_key} entry in ${ENDPOINT_CONFIG}"
        selected="${value}"
    done < "${ENDPOINT_CONFIG}"

    [[ -n "${selected}" ]] || fail "missing ${wanted_key} in ${ENDPOINT_CONFIG}"
    validate_ipv4 "${selected}" || fail "${wanted_key} must be a plain IPv4 address"
    printf '%s\n' "${selected}"
}

require_initialized_ca() {
    require_regular_file "${CA_DIR}/ca.crt"
    require_regular_file "${CA_DIR}/private/ca.key"
    require_regular_file "${CA_DIR}/index.txt"
    require_regular_file "${CA_DIR}/serial"
    require_regular_file "${CA_DIR}/crlnumber"
    require_regular_file "${PKI_ROOT}/crl.pem"
}

require_vpn_image() {
    require_command docker
    if ! docker image inspect "${VPN_IMAGE}" >/dev/null 2>&1; then
        fail "local image ${VPN_IMAGE} is missing; build it from deploy/image first"
    fi
}

run_openvpn_container() {
    local write_directory="$1"
    local endpoint_directory="$2"
    shift 2

    docker run --rm \
        --network none \
        --read-only \
        --cap-drop ALL \
        --security-opt no-new-privileges:true \
        --user "$(id -u):$(id -g)" \
        --tmpfs /tmp:mode=0700 \
        --volume "${write_directory}:/work" \
        --volume "${endpoint_directory}:/endpoint:ro" \
        --workdir /work \
        "${VPN_IMAGE}" \
        /usr/sbin/openvpn "$@"
}
