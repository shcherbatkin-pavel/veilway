#!/usr/bin/env bash

set -euo pipefail

readonly PROGRAM_NAME="${0##*/}"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"

cd -- "${REPOSITORY_ROOT}"

printf '%s\n' '[1/7] Bash syntax'
bash -n scripts/acceptance-ubuntu-direct scripts/audit-vm.sh scripts/check.sh scripts/container-smoke.sh scripts/diagnose-aws-container-client scripts/diagnose-aws-data-channel scripts/veilway-pki scripts/verify-aws-host-key.sh scripts/verify-client-profiles deploy/image/wait-for-interface

if command -v -- shellcheck >/dev/null 2>&1; then
    printf '%s\n' '[2/7] ShellCheck'
    shellcheck scripts/acceptance-ubuntu-direct scripts/audit-vm.sh scripts/check.sh scripts/container-smoke.sh scripts/diagnose-aws-container-client scripts/diagnose-aws-data-channel scripts/veilway-pki scripts/verify-aws-host-key.sh scripts/verify-client-profiles deploy/image/wait-for-interface
else
    printf '%s\n' '[2/7] ShellCheck skipped: command unavailable'
fi

printf '%s\n' '[3/7] Python syntax and CIDR validation'
python3 -m py_compile \
    scripts/pki-smoke.py \
    scripts/render-inventory.py \
    scripts/review-aws-direct-plan.py \
    scripts/review-yandex-multihop-plan.py \
    scripts/validate-network-plan.py \
    deploy/filter_plugins/veilway_network.py
valid_result="$(printf '%s\n' '{"vpc_cidr":"10.241.1.0/24","vpn_cidr":"10.242.10.0/24","future_multihop_cidr":"10.242.30.0/24","transit_cidr":"10.242.40.0/29","operator_cidrs_json":"[\"198.51.100.10/32\"]"}' | scripts/validate-network-plan.py)"
[[ "${valid_result}" == *'"valid": "true"'* ]] || {
    printf '%s\n' 'CIDR validator rejected the known-good fixture.' >&2
    exit 1
}
invalid_result="$(printf '%s\n' '{"vpc_cidr":"10.242.10.0/25","vpn_cidr":"10.242.10.0/24","future_multihop_cidr":"10.242.30.0/24","transit_cidr":"10.242.40.0/29","operator_cidrs_json":"[]"}' | scripts/validate-network-plan.py)"
[[ "${invalid_result}" == *'"valid": "false"'* ]] || {
    printf '%s\n' 'CIDR validator accepted an overlapping fixture.' >&2
    exit 1
}
ipv6_result="$(printf '%s\n' '{"vpc_cidr":"2001:db8:100::/56","vpn_cidr":"fd12:3456:789a:20::/64","future_multihop_cidr":"fd12:3456:789a:30::/64","transit_cidr":"fd12:3456:789a:40::/64","operator_cidrs_json":"[\"2001:db8:ffff::/64\"]"}' | scripts/validate-network-plan.py)"
[[ "${ipv6_result}" == *'"valid": "true"'* ]] || {
    printf '%s\n' 'CIDR validator rejected the known-good IPv6 fixture.' >&2
    exit 1
}
invalid_ipv6_result="$(printf '%s\n' '{"vpc_cidr":"2001:db8:100::/56","vpn_cidr":"fd12:3456:789a:20::/64","future_multihop_cidr":"fd12:3456:789a:30::/64","transit_cidr":"fd12:3456:789a:40::/64","operator_cidrs_json":"[\"fd12:3456:789a:20::1/128\"]"}' | scripts/validate-network-plan.py)"
[[ "${invalid_ipv6_result}" == *'"valid": "false"'* ]] || {
    printf '%s\n' 'CIDR validator accepted an overlapping IPv6 fixture.' >&2
    exit 1
}

if command -v -- terraform >/dev/null 2>&1; then
    printf '%s\n' '[4/7] Terraform formatting and validation'
    mapfile -t terraform_public_files < <(rg --files infra --glob '*.tf' | sort)
    ((${#terraform_public_files[@]} > 0)) || {
        printf '%s\n' 'No public Terraform files found.' >&2
        exit 1
    }
    terraform fmt -check "${terraform_public_files[@]}"
    for terraform_root in infra/aws infra/yandex; do
        if [[ -d "${terraform_root}/.terraform" ]]; then
            terraform -chdir="${terraform_root}" validate
        else
            printf 'Terraform validate skipped for %s: providers are not initialized.\n' "${terraform_root}"
        fi
    done
else
    printf '%s\n' '[4/7] Terraform checks skipped: command unavailable'
fi

if command -v -- ansible-playbook >/dev/null 2>&1; then
    printf '%s\n' '[5/7] Ansible syntax'
    ANSIBLE_CONFIG="${REPOSITORY_ROOT}/deploy/ansible.cfg" \
        ansible-playbook -i deploy/inventory.example.yml deploy/site.yml --syntax-check
    ANSIBLE_CONFIG="${REPOSITORY_ROOT}/deploy/ansible.cfg" \
        ansible-playbook -i deploy/inventory.example.yml deploy/preflight.yml --syntax-check
    ANSIBLE_CONFIG="${REPOSITORY_ROOT}/deploy/ansible.cfg" \
        ansible-playbook -i deploy/inventory.example.yml deploy/verify.yml --syntax-check
    ANSIBLE_CONFIG="${REPOSITORY_ROOT}/deploy/ansible.cfg" \
        ansible-playbook -i deploy/inventory.example.yml deploy/diagnose-egress.yml --syntax-check
else
    printf '%s\n' '[5/7] Ansible syntax skipped: command unavailable'
fi

printf '%s\n' '[6/7] Sensitive-path ignore policy'
for ignored_path in \
    audit-results/example/report.txt \
    client-profiles/example.ovpn \
    secrets/pki/ca/private/ca.key \
    infra/aws/terraform.tfstate \
    infra/yandex/terraform.tfvars \
    deploy/inventory.yml; do
    git check-ignore --quiet --no-index -- "${ignored_path}" || {
        printf 'Sensitive path is not ignored: %s\n' "${ignored_path}" >&2
        exit 1
    }
done
if git check-ignore --quiet --no-index -- operator-config/endpoints.conf.example; then
    printf '%s\n' 'Safe endpoint example is unexpectedly ignored.' >&2
    exit 1
fi

printf '%s\n' '[7/7] Diff whitespace and credential-shaped content'
git diff --check
if rg --hidden --glob '!.git/**' --glob '!audit-results/**' --glob '!client-profiles/**' \
    --glob '!secrets/**' \
    --multiline --quiet \
    -- '-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----\n[A-Za-z0-9+/]{32}' .; then
    printf '%s\n' 'Credential-shaped private key material found in public files.' >&2
    exit 1
fi
if rg --hidden --glob '!.git/**' --glob '!audit-results/**' --glob '!client-profiles/**' \
    --glob '!secrets/**' \
    --quiet -- 'AKIA[0-9A-Z]{16}' .; then
    printf '%s\n' 'Credential-shaped AWS access key found in public files.' >&2
    exit 1
fi

printf '%s\n' "${PROGRAM_NAME}: all available static checks passed"
