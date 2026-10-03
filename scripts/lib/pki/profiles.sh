#!/usr/bin/env bash
# Definitions only; loaded by scripts/veilway-pki.

write_profile() {
    local profile_path="$1"
    local profile_name="$2"
    local mode="$3"
    local server_identity="$4"
    local endpoint="$5"
    local port="$6"
    local client_certificate="$7"
    local client_key="$8"
    local tls_crypt_key="$9"

    {
        printf '%s\n' 'client'
        printf '%s\n' 'dev tun'
        printf '%s\n' 'proto udp4'
        printf 'remote %s %s\n' "${endpoint}" "${port}"
        printf '%s\n' 'nobind'
        printf '%s\n' 'persist-key'
        printf '%s\n' 'persist-tun'
        printf '%s\n' 'remote-cert-tls server'
        printf 'verify-x509-name %s name\n' "${server_identity}"
        printf '%s\n' 'tls-version-min 1.3'
        printf '%s\n' 'tls-cert-profile preferred'
        printf '%s\n' 'data-ciphers CHACHA20-POLY1305:AES-256-GCM:AES-128-GCM'
        printf '%s\n' 'auth SHA256'
        printf '%s\n' 'allow-compression no'
        if [[ "${mode}" == "yc-direct" ]]; then
            printf '%s\n' 'block-ipv6'
        fi
        printf '%s\n' 'verb 3'
        printf '%s\n' '<ca>'
        sed -n '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/p' "${CA_DIR}/ca.crt"
        printf '%s\n' '</ca>'
        printf '%s\n' '<cert>'
        sed -n '/-----BEGIN CERTIFICATE-----/,/-----END CERTIFICATE-----/p' "${client_certificate}"
        printf '%s\n' '</cert>'
        printf '%s\n' '<key>'
        sed -n '/-----BEGIN PRIVATE KEY-----/,/-----END PRIVATE KEY-----/p' "${client_key}"
        printf '%s\n' '</key>'
        printf '%s\n' '<tls-crypt-v2>'
        sed -n '/-----BEGIN OpenVPN tls-crypt-v2 client key-----/,/-----END OpenVPN tls-crypt-v2 client key-----/p' "${tls_crypt_key}"
        printf '%s\n' '</tls-crypt-v2>'
        printf '# Profile identity: %s\n' "${profile_name}"
    } > "${profile_path}"
    chmod 0600 -- "${profile_path}"
}

create_profile() (
    local device="$1"
    local mode="$2"
    shift 2
    local expiry
    local profile_name endpoint port endpoint_name server_identity endpoint_dir
    local client_dir profile_path profile_temp temp_dir request_file

    validate_device "${device}"
    validate_mode "${mode}"
    require_initialized_ca
    require_command python3
    expiry="$(python3 "${SCRIPT_DIR}/pki-expiry.py" --ca "${CA_DIR}/ca.crt" "$@")" || fail 'invalid certificate expiry'
    require_vpn_image
    endpoint="$(read_endpoint "${mode}")"
    port="$(port_for_mode "${mode}")"
    endpoint_name="$(server_directory_for_mode "${mode}")"
    server_identity="$(server_identity_for_mode "${mode}")"
    endpoint_dir="${ENDPOINTS_DIR}/${endpoint_name}"
    require_regular_file "${endpoint_dir}/server.crt"
    require_regular_file "${endpoint_dir}/tls-crypt-v2-server.key"

    profile_name="${device}-${mode}"
    client_dir="${CLIENTS_DIR}/${profile_name}"
    profile_path="${PROFILES_DIR}/${profile_name}.ovpn"
    [[ ! -e "${client_dir}" && ! -e "${profile_path}" ]] || fail "profile already exists: ${profile_name}"

    ensure_directory "${CLIENTS_DIR}" 0700
    ensure_directory "${TEMP_ROOT}" 0700
    ensure_directory "${PROFILES_DIR}" 0700
    temp_dir="$(mktemp -d -- "${TEMP_ROOT}/${profile_name}.XXXXXX")"
    chmod 0700 -- "${temp_dir}"
    trap 'rm -rf -- "${temp_dir}"' EXIT
    request_file="${temp_dir}/client.csr"
    profile_temp="${temp_dir}/${profile_name}.ovpn"

    openssl genpkey \
        -algorithm EC \
        -pkeyopt ec_paramgen_curve:P-256 \
        -out "${temp_dir}/client.key"
    openssl req \
        -new \
        -sha256 \
        -key "${temp_dir}/client.key" \
        -subj "/CN=${profile_name}" \
        -out "${request_file}"
    (
        cd -- "${CA_DIR}"
        openssl ca \
            -batch \
            -config "${OPENSSL_CONFIG}" \
            -extensions client_cert \
            -enddate "${expiry}" \
            -notext \
            -in "${request_file}" \
            -out "${temp_dir}/client.crt"
    )
    chmod 0644 -- "${temp_dir}/client.crt"
    python3 "${SCRIPT_DIR}/pki-expiry.py" --check-certificate "${temp_dir}/client.crt" --expected "${expiry}"

    run_openvpn_container "${temp_dir}" "${endpoint_dir}" \
        --tls-crypt-v2 /endpoint/tls-crypt-v2-server.key \
        --genkey tls-crypt-v2-client /work/tls-crypt-v2-client.key

    write_profile \
        "${profile_temp}" \
        "${profile_name}" \
        "${mode}" \
        "${server_identity}" \
        "${endpoint}" \
        "${port}" \
        "${temp_dir}/client.crt" \
        "${temp_dir}/client.key" \
        "${temp_dir}/tls-crypt-v2-client.key"

    grep -Fq -- '-----BEGIN CERTIFICATE-----' "${profile_temp}" || fail "generated profile is missing a certificate"
    grep -Fq -- '-----BEGIN PRIVATE KEY-----' "${profile_temp}" || fail "generated profile is missing a private key"
    grep -Fq -- '-----BEGIN OpenVPN tls-crypt-v2 client key-----' "${profile_temp}" || fail "generated profile is missing a tls-crypt-v2 key"

    ensure_directory "${client_dir}" 0700
    cp -- "${temp_dir}/client.crt" "${client_dir}/client.crt"
    chmod 0644 -- "${client_dir}/client.crt"
    mv -- "${profile_temp}" "${profile_path}"

    printf 'Created sensitive client profile: %s\n' "${profile_path}"
    printf '%s\n' 'Review it locally, import it through a trusted channel, and do not commit or publish it.'
)

update_profile_remotes() (
    local mode="$1"
    local endpoint port server_identity profile_filename profile_name profile_path temporary_directory
    local temporary_profile desired_remote legacy_remote remote_count updated=0
    local -a profile_names=()
    local -a profile_paths=()

    validate_mode "${mode}"
    endpoint="$(read_endpoint "${mode}")"
    port="$(port_for_mode "${mode}")"
    server_identity="$(server_identity_for_mode "${mode}")"
    desired_remote="remote ${endpoint} ${port}"
    legacy_remote="remote ${endpoint} 443"

    for command_name in awk chmod cmp git grep mktemp mv rm stat; do
        require_command "${command_name}"
    done
    [[ -d "${PROFILES_DIR}" && ! -L "${PROFILES_DIR}" ]] \
        || fail "client-profiles must be a regular directory"
    [[ "$(stat -c '%a' -- "${PROFILES_DIR}")" == '700' ]] \
        || fail "client-profiles directory mode must be 0700"

    temporary_directory="$(mktemp -d -- "${PROFILES_DIR}/.remote-update.XXXXXXXXXX")"
    chmod 0700 -- "${temporary_directory}"
    cleanup_remote_update() {
        case "${temporary_directory}" in
            "${PROFILES_DIR}"/.remote-update.*)
                rm -rf -- "${temporary_directory}"
                ;;
            *)
                fail 'refusing unexpected profile update cleanup path'
                ;;
        esac
    }
    trap cleanup_remote_update EXIT

    shopt -s nullglob
    profile_paths=("${PROFILES_DIR}"/*-"${mode}".ovpn)
    shopt -u nullglob
    ((${#profile_paths[@]} > 0)) \
        || fail "no protected profiles found for mode: ${mode}"

    for profile_path in "${profile_paths[@]}"; do
        profile_filename="${profile_path##*/}"
        profile_name="${profile_filename%.ovpn}"
        validate_profile_name "${profile_name}"
        temporary_profile="${temporary_directory}/${profile_name}.ovpn"
        profile_names+=("${profile_name}")

        require_regular_file "${profile_path}"
        [[ "$(stat -c '%a' -- "${profile_path}")" == '600' ]] \
            || fail "profile mode must be 0600: ${profile_name}"
        git -C "${REPOSITORY_ROOT}" check-ignore --quiet --no-index -- "${profile_path}" \
            || fail "profile is not ignored by Git: ${profile_name}"
        [[ "$(grep -Fxc -- "# Profile identity: ${profile_name}" "${profile_path}")" -eq 1 ]] \
            || fail "profile identity marker is invalid: ${profile_name}"
        [[ "$(grep -Fxc -- "verify-x509-name ${server_identity} name" "${profile_path}")" -eq 1 ]] \
            || fail "profile server identity constraint is invalid: ${profile_name}"
        remote_count="$(awk '$1 == "remote" { count++ } END { print count + 0 }' "${profile_path}")"
        [[ "${remote_count}" -eq 1 ]] \
            || fail "profile must contain exactly one remote directive: ${profile_name}"
        if ! grep -Fxq -- "${desired_remote}" "${profile_path}"; then
            [[ "${mode}" == 'aws-direct' ]] \
                && grep -Fxq -- "${legacy_remote}" "${profile_path}" \
                || fail "profile remote is neither the expected nor migratable value: ${profile_name}"
        fi

        awk -v replacement="${desired_remote}" '
            $1 == "remote" { print replacement; next }
            { print }
        ' "${profile_path}" > "${temporary_profile}"
        chmod 0600 -- "${temporary_profile}"
        [[ "$(grep -Fxc -- "${desired_remote}" "${temporary_profile}")" -eq 1 ]] \
            || fail "updated profile remote validation failed: ${profile_name}"
    done

    for profile_name in "${profile_names[@]}"; do
        profile_path="${PROFILES_DIR}/${profile_name}.ovpn"
        temporary_profile="${temporary_directory}/${profile_name}.ovpn"
        if ! cmp -s -- "${profile_path}" "${temporary_profile}"; then
            mv -- "${temporary_profile}" "${profile_path}"
            updated=$((updated + 1))
        fi
    done

    if ((updated > 0)); then
        printf 'Updated %s protected %s profile remote directives.\n' "${updated}" "${mode}"
    else
        printf 'Protected %s profile remote directives are already current.\n' "${mode}"
    fi
)
