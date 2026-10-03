#!/usr/bin/env python3
"""Review the Yandex multi-hop ingress Terraform plan without leaking values."""

from __future__ import annotations

import json
import sys
from typing import Any

from lib.operator_validation import ipv4_host_cidrs, rule_port


EXPECTED_RESOURCES = {
    "yandex_compute_instance.vpn",
    "yandex_vpc_address.vpn",
    "yandex_vpc_network.vpn",
    "yandex_vpc_security_group.vpn",
    "yandex_vpc_subnet.vpn",
}


def main() -> int:
    if sys.argv[1:] not in ([], ["--disable-multihop"]):
        print(
            "usage: review-yandex-multihop-plan.py [--disable-multihop]",
            file=sys.stderr,
        )
        return 2
    disable_multihop = sys.argv[1:] == ["--disable-multihop"]

    try:
        plan = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as error:
        print(f"plan JSON input: FAILED ({error})", file=sys.stderr)
        return 2

    changes = {
        item["address"]: item
        for item in plan.get("resource_changes", [])
        if item.get("mode") == "managed"
    }

    def after(address: str) -> dict[str, Any]:
        return changes.get(address, {}).get("change", {}).get("after", {}) or {}

    def before(address: str) -> dict[str, Any]:
        return changes.get(address, {}).get("change", {}).get("before", {}) or {}

    def actions(address: str) -> list[str]:
        return changes.get(address, {}).get("change", {}).get("actions", [])

    security_group = after("yandex_vpc_security_group.vpn")
    ingress = security_group.get("ingress") or []
    egress = security_group.get("egress") or []
    ingress_before = before("yandex_vpc_security_group.vpn").get("ingress") or []

    def udp_rules(port: int) -> list[dict[str, Any]]:
        return [
            rule
            for rule in ingress
            if str(rule.get("protocol", "")).upper() == "UDP"
            and rule_port(rule) == port
        ]

    ssh_rules = [
        rule
        for rule in ingress
        if str(rule.get("protocol", "")).upper() == "TCP"
        and rule_port(rule) == 22
    ]
    direct_rules = udp_rules(1194)
    multihop_rules = udp_rules(1195)
    transit_rules = udp_rules(1196)

    ssh_cidrs = (
        ssh_rules[0].get("v4_cidr_blocks") or [] if len(ssh_rules) == 1 else []
    )
    ssh_safe = ipv4_host_cidrs(ssh_cidrs, 2)

    public_direct = (
        len(direct_rules) == 1
        and direct_rules[0].get("v4_cidr_blocks") == ["0.0.0.0/0"]
    )
    public_multihop = (
        len(multihop_rules) == 1
        and multihop_rules[0].get("v4_cidr_blocks") == ["0.0.0.0/0"]
    )
    previous_multihop = [rule for rule in ingress_before if rule_port(rule) == 1195]
    unchanged_resources = EXPECTED_RESOURCES - {"yandex_vpc_security_group.vpn"}

    instance_before = before("yandex_compute_instance.vpn")
    instance_after = after("yandex_compute_instance.vpn")
    address_before = before("yandex_vpc_address.vpn")
    address_after = after("yandex_vpc_address.vpn")

    checks = {
        "resource allowlist": set(changes) == EXPECTED_RESOURCES,
        (
            "actions limited to removing the multi-hop security group rule"
            if disable_multihop
            else "actions limited to the multi-hop security group rule"
        ): (
            actions("yandex_vpc_security_group.vpn") == ["update"]
            and all(actions(address) == ["no-op"] for address in unchanged_resources)
            and (
                len(previous_multihop) == 1
                if disable_multihop
                else not previous_multihop
            )
        ),
        "SSH restricted to two IPv4 /32": ssh_safe,
        "OpenVPN Direct remains on public UDP/1194": public_direct,
        "OpenVPN multi-hop ingress state is correct": (
            not multihop_rules if disable_multihop else public_multihop
        ),
        "AWS transit UDP/1196 is closed on Yandex": not transit_rules,
        "expected ingress rule count": len(ingress) == (
            3 if disable_multihop else 4
        ),
        "IPv4 egress remains enabled": (
            len(egress) == 1
            and str(egress[0].get("protocol", "")).upper() == "ANY"
            and egress[0].get("v4_cidr_blocks") == ["0.0.0.0/0"]
        ),
        "existing VM retained": (
            bool(instance_before.get("id"))
            and instance_after.get("id") == instance_before.get("id")
            and actions("yandex_compute_instance.vpn") == ["no-op"]
        ),
        "VM remains without cloud IAM identity": not instance_after.get(
            "service_account_id"
        ),
        "existing static IPv4 retained": (
            bool(address_before.get("id"))
            and address_after.get("id") == address_before.get("id")
            and actions("yandex_vpc_address.vpn") == ["no-op"]
        ),
    }

    for name, passed in checks.items():
        print(f"{name}: {'passed' if passed else 'FAILED'}")

    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
