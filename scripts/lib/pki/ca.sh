#!/usr/bin/env bash
# Definitions only; loaded by scripts/veilway-pki.

initialize_ca() {
    require_command openssl
    require_regular_file "${OPENSSL_CONFIG}"
    [[ ! -e "${CA_DIR}" ]] || fail "CA directory already exists; inspect it manually before retrying: ${CA_DIR}"

    ensure_directory "${SECRETS_ROOT}" 0700
    ensure_directory "${PKI_ROOT}" 0700
    ensure_directory "${CA_DIR}" 0700
    ensure_directory "${CA_DIR}/private" 0700
    ensure_directory "${CA_DIR}/newcerts" 0700
    ensure_directory "${CLIENTS_DIR}" 0700
    ensure_directory "${ENDPOINTS_DIR}" 0700
    ensure_directory "${TEMP_ROOT}" 0700
    ensure_directory "${PROFILES_DIR}" 0700

    : > "${CA_DIR}/index.txt"
    printf '1000\n' > "${CA_DIR}/serial"
    printf '1000\n' > "${CA_DIR}/crlnumber"
    chmod 0600 -- "${CA_DIR}/index.txt" "${CA_DIR}/serial" "${CA_DIR}/crlnumber"

    printf '%s\n' 'Creating the encrypted offline CA key. Enter a strong unique passphrase when OpenSSL prompts.'
    openssl genpkey \
        -algorithm EC \
        -pkeyopt ec_paramgen_curve:P-256 \
        -aes-256-cbc \
        -out "${CA_DIR}/private/ca.key"
    chmod 0600 -- "${CA_DIR}/private/ca.key"

    openssl req \
        -config "${OPENSSL_CONFIG}" \
        -new \
        -x509 \
        -key "${CA_DIR}/private/ca.key" \
        -sha256 \
        -days 3650 \
        -extensions v3_ca \
        -out "${CA_DIR}/ca.crt"
    chmod 0644 -- "${CA_DIR}/ca.crt"

    (
        cd -- "${CA_DIR}"
        openssl ca -config "${OPENSSL_CONFIG}" -gencrl -out "${PKI_ROOT}/crl.pem"
    )
    chmod 0644 -- "${PKI_ROOT}/crl.pem"
    printf 'Initialized offline CA under: %s\n' "${CA_DIR}"
}
