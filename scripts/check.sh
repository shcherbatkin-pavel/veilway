#!/usr/bin/env bash

set -euo pipefail

readonly PROGRAM_NAME="${0##*/}"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd -P)"

cd -- "${REPOSITORY_ROOT}"

shell_files=(
    scripts/acceptance-ubuntu-direct scripts/audit-vm.sh scripts/check.sh
    scripts/container-smoke.sh scripts/test-profile-security.sh scripts/diagnose-aws-container-client
    scripts/diagnose-aws-data-channel scripts/test-control-plane.sh scripts/test-pki-service.sh scripts/test-profile-api.sh scripts/test-crl-mount.sh scripts/test-profile-panel.sh scripts/veilway-pki
    scripts/verify-aws-host-key.sh scripts/verify-client-profiles deploy/image/wait-for-interface
)
mapfile -t pki_shell_files < <(rg --files scripts/lib/pki --glob '*.sh' | sort)
shell_files+=("${pki_shell_files[@]}")
printf '%s\n' '[1/9] Bash syntax'
for shell_file in "${shell_files[@]}"; do
    bash -n "${shell_file}"
done

if command -v -- shellcheck >/dev/null 2>&1; then
    printf '%s\n' '[2/9] ShellCheck'
    shellcheck --external-sources --source-path=SCRIPTDIR "${shell_files[@]}"
else
    printf '%s\n' '[2/9] ShellCheck skipped: command unavailable'
fi

printf '%s\n' '[3/9] Python syntax and CIDR validation'
python3 -m py_compile \
    web/frontend/tests/browser.py \
    scripts/test-panel-proxy.py \
    scripts/test-profile-rollout.py \
    scripts/control-postgres-smoke.py \
    scripts/pki-smoke.py \
    scripts/pki-expiry.py \
    scripts/benchmark-pki.py \
    scripts/test-pki-benchmark.py \
    scripts/test-pki-expiry.py \
    scripts/test-operator-tools.py \
    scripts/check-public-diff.py \
    scripts/render-inventory.py \
    scripts/review-aws-direct-plan.py \
    scripts/review-yandex-multihop-plan.py \
    scripts/deploy-web-control.py \
    scripts/validate-control-compose.py \
    scripts/validate-network-plan.py \
    deploy/filter_plugins/veilway_network.py \
    deploy/roles/veilway_crl_agent/files/veilway-crl-agent \
    deploy/roles/veilway_heartbeat/files/veilway-heartbeat-agent
mapfile -t control_python_files < <(rg --files web/backend web/pki --glob '*.py' | sort)
mapfile -t operator_python_files < <(rg --files scripts/lib --glob '*.py' | sort)
python3 -m py_compile "${control_python_files[@]}" "${operator_python_files[@]}"
python3 scripts/test-pki-expiry.py
python3 scripts/test-pki-benchmark.py
python3 scripts/test-operator-tools.py
python3 scripts/test-profile-rollout.py
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
    printf '%s\n' '[4/9] Terraform formatting and validation'
    mapfile -t terraform_public_files < <(rg --files infra --glob '*.tf' | sort)
    ((${#terraform_public_files[@]} > 0)) || {
        printf '%s\n' 'No public Terraform files found.' >&2
        exit 1
    }
    terraform fmt -check "${terraform_public_files[@]}"
    for terraform_root in infra/aws infra/yandex infra/aws-management infra/yandex-web; do
        if [[ -d "${terraform_root}/.terraform" ]]; then
            terraform -chdir="${terraform_root}" validate
        else
            printf 'Terraform validate skipped for %s: providers are not initialized.\n' "${terraform_root}"
        fi
    done
    if rg --quiet '^resource "aws_iam_access_key"' infra/aws-management; then
        printf '%s\n' 'AWS management root must not create an access key.' >&2
        exit 1
    fi
else
    printf '%s\n' '[4/9] Terraform checks skipped: command unavailable'
fi

if command -v -- ansible-playbook >/dev/null 2>&1; then
    printf '%s\n' '[5/9] Ansible syntax'
    vpn_playbooks=(site preflight verify diagnose-egress)
    control_playbooks=(control-web heartbeat crl-agents)
    for playbook in "${vpn_playbooks[@]}"; do
        ANSIBLE_LOCAL_TEMP=/tmp/veilway-ansible-local \
            ANSIBLE_CONFIG="${REPOSITORY_ROOT}/deploy/ansible.cfg" \
            ansible-playbook -i deploy/inventory.example.yml "deploy/${playbook}.yml" --syntax-check
    done
    for playbook in "${control_playbooks[@]}"; do
        ANSIBLE_LOCAL_TEMP=/tmp/veilway-ansible-local \
            ANSIBLE_CONFIG="${REPOSITORY_ROOT}/deploy/ansible.cfg" \
            ansible-playbook -i deploy/control-inventory.example.yml "deploy/${playbook}.yml" --syntax-check
    done
else
    printf '%s\n' '[5/9] Ansible syntax skipped: command unavailable'
fi

printf '%s\n' '[6/9] Control plane Compose surface'
if command -v -- docker >/dev/null 2>&1; then
    docker compose -f web/compose.yaml config --format json \
        | python3 scripts/validate-control-compose.py
else
    printf '%s\n' 'Compose validation skipped: Docker unavailable'
fi

printf '%s\n' '[7/9] Frontend build inputs'
test -f web/frontend/package-lock.json
if [[ -d web/frontend/node_modules ]]; then
    npm --prefix web/frontend run typecheck
    npm --prefix web/frontend run test
else
    printf '%s\n' 'Frontend checks skipped: node_modules is absent'
fi

printf '%s\n' '[8/9] Sensitive-path ignore policy'
for ignored_path in \
    .env \
    audit-results/example/report.txt \
    client-profiles/example.ovpn \
    secrets/pki/ca/private/ca.key \
    infra/aws/terraform.tfstate \
    infra/yandex/terraform.tfvars \
    deploy/inventory.yml \
    deploy/control-inventory.yml; do
    git check-ignore --quiet --no-index -- "${ignored_path}" || {
        printf 'Sensitive path is not ignored: %s\n' "${ignored_path}" >&2
        exit 1
    }
done
if git check-ignore --quiet --no-index -- operator-config/endpoints.conf.example; then
    printf '%s\n' 'Safe endpoint example is unexpectedly ignored.' >&2
    exit 1
fi
if git check-ignore --quiet --no-index -- .env.example; then
    printf '%s\n' 'Safe environment example is unexpectedly ignored.' >&2
    exit 1
fi

printf '%s\n' '[9/9] Diff whitespace and changed public-file credential scan'
git diff --check
python3 scripts/check-public-diff.py

printf '%s\n' 'Container smoke tests are separate: scripts/test-control-plane.sh --build, scripts/test-pki-service.sh --build, scripts/pki-smoke.py, scripts/container-smoke.sh'
printf '%s\n' "${PROGRAM_NAME}: all available static checks passed"
