#!/usr/bin/env bash
# Definitions only; loaded by scripts/veilway-pki.

create_server() {
    local mode="$1"
    local endpoint_dir="${ENDPOINTS_DIR}/${mode}"
    local request_file

    validate_server_mode "${mode}"
    require_initialized_ca
    require_vpn_image
    [[ ! -e "${endpoint_dir}" ]] || fail "endpoint already exists: ${endpoint_dir}"
    ensure_directory "${endpoint_dir}" 0700
    request_file="${endpoint_dir}/server.csr"

    openssl genpkey \
        -algorithm EC \
        -pkeyopt ec_paramgen_curve:P-256 \
        -out "${endpoint_dir}/server.key"
    chmod 0600 -- "${endpoint_dir}/server.key"
    openssl req \
        -new \
        -sha256 \
        -key "${endpoint_dir}/server.key" \
        -subj "/CN=${mode}" \
        -out "${request_file}"

    (
        cd -- "${CA_DIR}"
        openssl ca \
            -batch \
            -config "${OPENSSL_CONFIG}" \
            -extensions server_cert \
            -days 365 \
            -notext \
            -in "${request_file}" \
            -out "${endpoint_dir}/server.crt"
    )
    chmod 0644 -- "${endpoint_dir}/server.crt"

    run_openvpn_container "${endpoint_dir}" "${endpoint_dir}" \
        --genkey tls-crypt-v2-server /work/tls-crypt-v2-server.key
    chmod 0600 -- "${endpoint_dir}/tls-crypt-v2-server.key"
    rm -- "${request_file}"
    printf 'Created server identity for %s under: %s\n' "${mode}" "${endpoint_dir}"
}

create_transit_client() {
    local client_dir="${ENDPOINTS_DIR}/yc-transit"
    local server_dir="${ENDPOINTS_DIR}/aws-transit"
    local request_file

    require_initialized_ca
    require_vpn_image
    require_regular_file "${server_dir}/server.crt"
    require_regular_file "${server_dir}/tls-crypt-v2-server.key"
    [[ ! -e "${client_dir}" ]] || fail "transit client already exists: ${client_dir}"
    ensure_directory "${client_dir}" 0700
    request_file="${client_dir}/client.csr"

    openssl genpkey \
        -algorithm EC \
        -pkeyopt ec_paramgen_curve:P-256 \
        -out "${client_dir}/client.key"
    chmod 0600 -- "${client_dir}/client.key"
    openssl req \
        -new \
        -sha256 \
        -key "${client_dir}/client.key" \
        -subj '/CN=yc-transit' \
        -out "${request_file}"
    (
        cd -- "${CA_DIR}"
        openssl ca \
            -batch \
            -config "${OPENSSL_CONFIG}" \
            -extensions client_cert \
            -days 365 \
            -notext \
            -in "${request_file}" \
            -out "${client_dir}/client.crt"
    )
    chmod 0644 -- "${client_dir}/client.crt"

    run_openvpn_container "${client_dir}" "${server_dir}" \
        --tls-crypt-v2 /endpoint/tls-crypt-v2-server.key \
        --genkey tls-crypt-v2-client /work/tls-crypt-v2-client.key
    chmod 0600 -- "${client_dir}/tls-crypt-v2-client.key"
    rm -- "${request_file}"
    printf 'Created Yandex-to-AWS transit client identity under: %s\n' "${client_dir}"
}
