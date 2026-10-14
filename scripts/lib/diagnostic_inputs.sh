#!/usr/bin/env bash
# Definitions only. Read one explicit endpoint input without evaluating it.
read_aws_endpoint() {
    local endpoint_config="$1"
    local key value selected=""
    while IFS='=' read -r key value; do
        [[ "${key}" == 'aws_direct_endpoint' ]] || continue
        [[ -z "${selected}" ]] || return 1
        selected="${value}"
    done < "${endpoint_config}"
    [[ -n "${selected}" ]] || return 1
    printf '%s\n' "${selected}"
}
