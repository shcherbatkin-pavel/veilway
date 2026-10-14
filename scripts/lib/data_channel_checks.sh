#!/usr/bin/env bash
# Definitions only. Evaluate already collected counters; no host or file access.
report_data_channel_checks() {
    local -n observations="$1"
    local diagnostic_failed=false
    local server_rx_before="${2}"
    local server_tx_before="${3}"
    local server_rx_after="${4}"
    local server_tx_after="${5}"
    local ingress_before="${6}"
    local ingress_after="${7}"
    local raw_ingress_before="${8}"
    local raw_ingress_after="${9}"
    local live_nested_path_checked="${10}"
    local live_nested_path_used="${11}"
    local live_outer_route_compared="${12}"
    local live_outer_route_stable="${13}"
    local live_outbound_capture_performed="${14}"
    local live_outbound_burst="${15}"
    local live_remote_sampled="${16}"
    local live_raw_ingress_before="${17}"
    local live_ingress_before="${18}"
    if [[ "${observations[9]}" == true ]]; then
        printf '%s\n' 'AWS outer endpoint route and source: passed'
    else
        printf '%s\n' 'AWS outer endpoint route or source: FAILED'
        diagnostic_failed=true
    fi
    if [[ "${live_nested_path_checked}" == true ]]; then
        if [[ "${live_nested_path_used}" == true ]]; then
            printf '%s\n' \
                'AWS outer transport used the existing VPN tunnel: passed'
        else
            printf '%s\n' \
                'AWS outer transport used the existing VPN tunnel: FAILED'
            diagnostic_failed=true
        fi
    fi
    if [[ "${live_outer_route_compared}" == true ]]; then
        if [[ "${live_outer_route_stable}" == true ]]; then
            printf '%s\n' \
                'AWS outer route selection remained stable after activation: passed'
        else
            printf '%s\n' \
                'AWS outer route selection changed after activation: FAILED'
            diagnostic_failed=true
        fi
    fi
    if ((observations[5] >= 10)); then
        printf '%s\n' 'client sent packets into TUN: passed'
    else
        printf '%s\n' 'client sent packets into TUN: FAILED'
        diagnostic_failed=true
    fi
    if ((observations[6] >= 10)); then
        printf '%s\n' 'client emitted UDP datagrams after tunnel traffic: passed'
    else
        printf '%s\n' 'client emitted UDP datagrams after tunnel traffic: FAILED'
        diagnostic_failed=true
    fi
    if [[ "${live_outbound_capture_performed}" == true ]]; then
        if [[ "${live_outbound_burst}" == true ]]; then
            printf '%s\n' \
                'client emitted an OpenVPN data-channel burst with the expected source: passed'
        else
            printf '%s\n' \
                'client emitted an OpenVPN data-channel burst with the expected source: FAILED'
            diagnostic_failed=true
        fi
    fi
    if [[ "${live_remote_sampled}" == true ]]; then
        if ((live_raw_ingress_before > observations[4])); then
            printf '%s\n' \
                'AWS pre-conntrack counter counted activation UDP: passed'
        else
            printf '%s\n' \
                'AWS pre-conntrack counter counted activation UDP: FAILED'
            diagnostic_failed=true
        fi
        if ((live_ingress_before > observations[3])); then
            printf '%s\n' \
                'AWS firewall counted OpenVPN activation UDP: passed'
        else
            printf '%s\n' \
                'AWS firewall counted OpenVPN activation UDP: FAILED'
            diagnostic_failed=true
        fi
    fi
    if ((raw_ingress_after - raw_ingress_before >= 10)); then
        printf '%s\n' \
            'AWS pre-conntrack counter received the OpenVPN UDP burst: passed'
    elif ((raw_ingress_after > raw_ingress_before)); then
        printf '%s\n' \
            'AWS pre-conntrack counter received only sparse OpenVPN UDP: FAILED'
        diagnostic_failed=true
    else
        printf '%s\n' \
            'AWS pre-conntrack counter received no OpenVPN UDP: FAILED'
        diagnostic_failed=true
    fi
    if ((ingress_after - ingress_before >= 10)); then
        printf '%s\n' 'AWS firewall received the OpenVPN UDP burst: passed'
    elif ((ingress_after > ingress_before)); then
        printf '%s\n' 'AWS firewall received only sparse OpenVPN UDP: FAILED'
        diagnostic_failed=true
    else
        printf '%s\n' 'AWS firewall received no OpenVPN UDP during probe: FAILED'
        diagnostic_failed=true
    fi
    if ((server_rx_after > server_rx_before)); then
        printf '%s\n' 'AWS TUN received client packets: passed'
    else
        printf '%s\n' 'AWS TUN received client packets: FAILED'
        diagnostic_failed=true
    fi
    if ((server_tx_after > server_tx_before)); then
        printf '%s\n' 'AWS TUN sent reply packets: passed'
    else
        printf '%s\n' 'AWS TUN sent reply packets: FAILED'
        diagnostic_failed=true
    fi
    if ((observations[7] > 0)); then
        printf '%s\n' 'client TUN received reply packets: passed'
    else
        printf '%s\n' 'client TUN received reply packets: FAILED'
        diagnostic_failed=true
    fi
    if [[ "${observations[8]}" == true ]]; then
        printf '%s\n' 'AWS tunnel gateway ping: passed'
    else
        printf '%s\n' 'AWS tunnel gateway ping: FAILED'
        diagnostic_failed=true
    fi

    [[ "${diagnostic_failed}" == false ]]
}
