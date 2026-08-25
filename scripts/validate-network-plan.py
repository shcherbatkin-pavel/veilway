#!/usr/bin/env python3
"""Validate Terraform network inputs without exposing them in process argv."""

import ipaddress
import json
import sys


def result(valid, message):
    json.dump({"valid": "true" if valid else "false", "message": message}, sys.stdout)
    sys.stdout.write("\n")


def main():
    try:
        query = json.load(sys.stdin)
        networks = {
            "Direct VPN": ipaddress.ip_network(query["vpn_cidr"], strict=True)
        }
        if query.get("vpc_cidr"):
            networks["VPC"] = ipaddress.ip_network(query["vpc_cidr"], strict=True)
        if query.get("future_multihop_cidr"):
            networks["future multi-hop"] = ipaddress.ip_network(
                query["future_multihop_cidr"], strict=True
            )
        operator_values = json.loads(query["operator_cidrs_json"])
        operator_networks = [
            ipaddress.ip_network(value, strict=True) for value in operator_values
        ]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        result(False, f"invalid network input: {exc}")
        return

    direct_network = networks["Direct VPN"]
    if any(network.version != direct_network.version for network in networks.values()):
        result(False, "managed network CIDRs must use the same address family")
        return
    if any(network.version != direct_network.version for network in operator_networks):
        result(False, "operator CIDRs use the wrong address family")
        return

    items = list(networks.items())
    for index, (first_name, first) in enumerate(items):
        for second_name, second in items[index + 1 :]:
            if first.overlaps(second):
                result(False, f"{first_name} CIDR overlaps {second_name} CIDR")
                return

    for operator_network in operator_networks:
        for network_name, network in networks.items():
            if network.overlaps(operator_network):
                result(False, f"{network_name} CIDR overlaps an operator CIDR")
                return

    result(True, "network CIDRs are canonical and non-overlapping")


if __name__ == "__main__":
    main()
