#!/usr/bin/env python3
"""Review an aws-direct Terraform plan without printing sensitive values."""

from __future__ import annotations

import ipaddress
import json
import sys
from typing import Any

from lib.operator_validation import ipv4_host_cidrs


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
    if sys.argv[1:] not in (
        [],
        ["--recovery"],
        ["--port-rollback"],
        ["--enable-multihop"],
        ["--disable-multihop"],
    ):
        print(
            "usage: review-aws-direct-plan.py "
            "[--recovery|--port-rollback|--enable-multihop|--disable-multihop]",
            file=sys.stderr,
        )
        return 2

    recovery = sys.argv[1:] == ["--recovery"]
    port_rollback = sys.argv[1:] == ["--port-rollback"]
    enable_multihop = sys.argv[1:] == ["--enable-multihop"]
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

    vpc = after("aws_vpc.vpn")
    subnet = after("aws_subnet.vpn")
    routes = after("aws_route_table.vpn").get("route") or []
    instance = after("aws_instance.vpn")
    instance_before = before("aws_instance.vpn")
    metadata = one(instance.get("metadata_options"))
    root = one(instance.get("root_block_device"))
    security_group = after("aws_security_group.vpn")
    ingress = security_group.get("ingress") or []
    egress = security_group.get("egress") or []
    ingress_before = before("aws_security_group.vpn").get("ingress") or []

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
    transit_rules = [
        rule
        for rule in ingress
        if rule.get("protocol") == "udp"
        and rule.get("from_port") == 1196
        and rule.get("to_port") == 1196
    ]

    ssh_cidrs = ssh_rules[0].get("cidr_blocks") or [] if len(ssh_rules) == 1 else []
    ssh_cidrs_are_hosts = ipv4_host_cidrs(ssh_cidrs, 2)

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
    transit_cidrs = (
        transit_rules[0].get("cidr_blocks") or []
        if len(transit_rules) == 1
        else []
    )
    try:
        transit_safe = len(transit_cidrs) == 1 and (
            ipaddress.ip_network(transit_cidrs[0], strict=True).version == 4
            and ipaddress.ip_network(transit_cidrs[0], strict=True).prefixlen == 32
        )
    except ValueError:
        transit_safe = False
    transit_expected = enable_multihop or (recovery and bool(transit_rules))

    public_ipv4_check_name = "no automatic public IPv4"
    public_ipv4_safe = instance.get("associate_public_ip_address") is False
    if port_rollback or enable_multihop or disable_multihop:
        public_ipv4_check_name = "EC2 public IPv4 attachment observation unchanged"
        observed_before = instance_before.get("associate_public_ip_address")
        observed_after = instance.get("associate_public_ip_address")
        public_ipv4_safe = (
            isinstance(observed_before, bool)
            and isinstance(observed_after, bool)
            and observed_after == observed_before
            and bool(instance_before.get("id"))
            and instance.get("id") == instance_before.get("id")
            and actions("aws_instance.vpn") == ["no-op"]
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
    elif port_rollback:
        unchanged_resources = EXPECTED_RESOURCES - {"aws_security_group.vpn"}
        action_check_name = "actions limited to the security group port rollback"
        legacy_vpn_rules = [
            rule
            for rule in ingress_before
            if rule.get("protocol") == "udp"
            and rule.get("from_port") == 443
            and rule.get("to_port") == 443
            and rule.get("cidr_blocks") == ["0.0.0.0/0"]
            and not (rule.get("ipv6_cidr_blocks") or [])
        ]
        actions_are_safe = (
            actions("aws_security_group.vpn") == ["update"]
            and all(actions(address) == ["no-op"] for address in unchanged_resources)
            and len(legacy_vpn_rules) == 1
        )
    elif enable_multihop:
        unchanged_resources = EXPECTED_RESOURCES - {"aws_security_group.vpn"}
        action_check_name = "actions limited to the transit security group rule"
        previous_transit_rules = [
            rule
            for rule in ingress_before
            if rule.get("protocol") == "udp"
            and rule.get("from_port") == 1196
            and rule.get("to_port") == 1196
        ]
        actions_are_safe = (
            actions("aws_security_group.vpn") == ["update"]
            and all(actions(address) == ["no-op"] for address in unchanged_resources)
            and not previous_transit_rules
        )
    elif disable_multihop:
        unchanged_resources = EXPECTED_RESOURCES - {"aws_security_group.vpn"}
        action_check_name = "actions limited to removing the transit security group rule"
        previous_transit_rules = [
            rule
            for rule in ingress_before
            if rule.get("protocol") == "udp"
            and rule.get("from_port") == 1196
            and rule.get("to_port") == 1196
            and len(rule.get("cidr_blocks") or []) == 1
        ]
        actions_are_safe = (
            actions("aws_security_group.vpn") == ["update"]
            and all(actions(address) == ["no-op"] for address in unchanged_resources)
            and len(previous_transit_rules) == 1
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
        "multi-hop client ingress UDP/1195 closed": all(
            rule.get("from_port") != 1195 and rule.get("to_port") != 1195
            for rule in ingress
        ),
        "OpenVPN transit only from one IPv4 /32": (
            transit_safe if transit_expected else not transit_rules
        ),
        "expected ingress rule count": len(ingress) == (5 if transit_expected else 4),
        "dual-stack egress": (
            len(egress) == 1
            and egress[0].get("protocol") == "-1"
            and egress[0].get("cidr_blocks") == ["0.0.0.0/0"]
            and egress[0].get("ipv6_cidr_blocks") == ["::/0"]
        ),
        "VPN forwarding enabled": instance.get("source_dest_check") is False,
        public_ipv4_check_name: public_ipv4_safe,
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

    if recovery or port_rollback or enable_multihop or disable_multihop:
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
