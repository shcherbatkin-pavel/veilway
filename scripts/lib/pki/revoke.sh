#!/usr/bin/env bash
# Definitions only; loaded by scripts/veilway-pki.

revoke_profile() {
    local profile_name="$1"
    local certificate_path="${CLIENTS_DIR}/${profile_name}/client.crt"

    validate_profile_name "${profile_name}"
    require_initialized_ca
    require_regular_file "${certificate_path}"

    (
        cd -- "${CA_DIR}"
        openssl ca -config "${OPENSSL_CONFIG}" -revoke "${certificate_path}"
        openssl ca -config "${OPENSSL_CONFIG}" -gencrl -out "${PKI_ROOT}/crl.pem"
    )
    chmod 0644 -- "${PKI_ROOT}/crl.pem"
    printf 'Revoked %s and updated: %s\n' "${profile_name}" "${PKI_ROOT}/crl.pem"
    printf '%s\n' 'Re-run the reviewed Ansible deployment for both new endpoints.'
}
