#!/usr/bin/env python3
"""Review an aws-direct Terraform plan without printing sensitive values."""

from __future__ import annotations

import ipaddress
import json
import sys
from typing import Any


EXPECTED_RESOURCES = {
    "aws_eip.vpn",
    "aws_instance.vpn",
    "aws_internet_gateway.vpn",
    "aws_key_pair.operator",
    "aws_route_table.vpn",
    "aws_route_table_association.vpn",
    "aws_security_group.vpn",
    "aws_subnet.vpn",
    "aws_vpc.vpn",
    "random_id.vpn_ula_global_id",
}


def one(value: Any) -> dict[str, Any]:
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        return value[0]
    if isinstance(value, dict):
        return value
    return {}


def main() -> int:
    if sys.argv[1:] not in ([], ["--recovery"]):
        print("usage: review-aws-direct-plan.py [--recovery]", file=sys.stderr)
        return 2

    recovery = sys.argv[1:] == ["--recovery"]

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

    vpc = after("aws_vpc.vpn")
    subnet = after("aws_subnet.vpn")
    routes = after("aws_route_table.vpn").get("route") or []
    instance = after("aws_instance.vpn")
    metadata = one(instance.get("metadata_options"))
    root = one(instance.get("root_block_device"))
    security_group = after("aws_security_group.vpn")
    ingress = security_group.get("ingress") or []
    egress = security_group.get("egress") or []

    ssh_rules = [
        rule
        for rule in ingress
        if rule.get("protocol") == "tcp"
        and rule.get("from_port") == 22
        and rule.get("to_port") == 22
    ]
    vpn_rules = [
        rule
        for rule in ingress
        if rule.get("protocol") == "udp"
        and rule.get("from_port") == 1194
        and rule.get("to_port") == 1194
    ]

    ssh_cidrs = ssh_rules[0].get("cidr_blocks") or [] if len(ssh_rules) == 1 else []
    try:
        ssh_cidrs_are_hosts = len(ssh_cidrs) == 2 and all(
            ipaddress.ip_network(cidr, strict=True).version == 4
            and ipaddress.ip_network(cidr, strict=True).prefixlen == 32
            for cidr in ssh_cidrs
        )
    except ValueError:
        ssh_cidrs_are_hosts = False

    ssh_safe = (
        len(ssh_rules) == 1
        and ssh_cidrs_are_hosts
        and not (ssh_rules[0].get("ipv6_cidr_blocks") or [])
    )
    vpn_safe = (
        len(vpn_rules) == 1
        and vpn_rules[0].get("cidr_blocks") == ["0.0.0.0/0"]
        and not (vpn_rules[0].get("ipv6_cidr_blocks") or [])
    )

    if recovery:
        unchanged_resources = EXPECTED_RESOURCES - {
            "aws_eip.vpn",
            "aws_instance.vpn",
        }
        action_check_name = "recovery actions limited to VM replacement and EIP update"
        actions_are_safe = (
            actions("aws_instance.vpn") == ["delete", "create"]
            and actions("aws_eip.vpn") == ["update"]
            and all(actions(address) == ["no-op"] for address in unchanged_resources)
        )
    else:
        action_check_name = "create-only actions"
        actions_are_safe = all(
            item.get("change", {}).get("actions") == ["create"]
            for item in changes.values()
        )

    checks = {
        "resource allowlist": set(changes) == EXPECTED_RESOURCES,
        action_check_name: actions_are_safe,
        "London dual-stack VPC": (
            vpc.get("assign_generated_ipv6_cidr_block") is True
            and subnet.get("availability_zone") == "eu-west-2a"
            and subnet.get("assign_ipv6_address_on_creation") is True
            and subnet.get("map_public_ip_on_launch") is False
            and len(routes) == 2
            and any(route.get("cidr_block") == "0.0.0.0/0" for route in routes)
            and any(route.get("ipv6_cidr_block") == "::/0" for route in routes)
        ),
        "SSH restricted to two IPv4 /32": ssh_safe,
        "OpenVPN only on UDP/1194 IPv4": vpn_safe,
        "future ports 1195/1196 closed": all(
            rule.get("from_port") not in (1195, 1196)
            and rule.get("to_port") not in (1195, 1196)
            for rule in ingress
        ),
        "expected four ingress rules": len(ingress) == 4,
        "dual-stack egress": (
            len(egress) == 1
            and egress[0].get("protocol") == "-1"
            and egress[0].get("cidr_blocks") == ["0.0.0.0/0"]
            and egress[0].get("ipv6_cidr_blocks") == ["::/0"]
        ),
        "VPN forwarding enabled": instance.get("source_dest_check") is False,
        "no automatic public IPv4": (
            instance.get("associate_public_ip_address") is False
        ),
        "one public IPv6": instance.get("ipv6_address_count") == 1,
        "no cloud IAM identity": not instance.get("iam_instance_profile"),
        "no cloud-init secrets": not instance.get("user_data"),
        "minimal IMDSv2 for first-boot SSH key": (
            metadata.get("http_endpoint") == "enabled"
            and metadata.get("http_tokens") == "required"
            and metadata.get("http_put_response_hop_limit") == 1
            and metadata.get("http_protocol_ipv6") == "disabled"
            and metadata.get("instance_metadata_tags") == "disabled"
        ),
        "encrypted 20 GiB gp3 root": (
            root.get("encrypted") is True
            and root.get("volume_type") == "gp3"
            and root.get("volume_size") == 20
        ),
        "instance type t3.small": instance.get("instance_type") == "t3.small",
        "Elastic IP is VPC-scoped": after("aws_eip.vpn").get("domain") == "vpc",
    }

    if recovery:
        eip_before = before("aws_eip.vpn")
        eip_after = after("aws_eip.vpn")
        checks["existing Elastic IP allocation retained"] = (
            bool(eip_before.get("allocation_id"))
            and eip_after.get("allocation_id") == eip_before.get("allocation_id")
            and eip_after.get("public_ip") == eip_before.get("public_ip")
        )

    for name, passed in checks.items():
        status = "passed" if passed else "FAILED"
        print(f"{name}: {status}")

    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
