#!/usr/bin/env python3
"""Render the ignored Direct VPN inventory from protected local state."""

from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
INVENTORY_PATH = REPOSITORY_ROOT / "deploy" / "inventory.yml"
ENDPOINT_CONFIG = REPOSITORY_ROOT / "operator-config" / "endpoints.conf"
OPENVPN_PORTS = {"yc-direct": 1194, "aws-direct": 1194}
MULTIHOP_INGRESS_PORT = 1195
TRANSIT_PORT = 1196


class InventoryError(RuntimeError):
    """Raised when protected local inputs do not meet the deployment contract."""


def require_protected_file(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise InventoryError(f"missing regular protected file: {path.name}")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise InventoryError(f"protected file mode must be 0600: {path.name}")


def require_ignored(path: Path) -> None:
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", "--", str(path)],
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise InventoryError(f"sensitive output is not ignored by Git: {path.name}")


def load_state(cloud: str) -> dict[str, Any]:
    path = REPOSITORY_ROOT / "infra" / cloud / "terraform.tfstate"
    require_protected_file(path)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise InventoryError(f"cannot read {cloud} Terraform state") from error
    if not isinstance(state, dict):
        raise InventoryError(f"invalid {cloud} Terraform state")
    return state


def output_value(state: dict[str, Any], name: str) -> Any:
    try:
        return state["outputs"][name]["value"]
    except (KeyError, TypeError) as error:
        raise InventoryError(f"missing Terraform output: {name}") from error


def resource_attributes(
    state: dict[str, Any], resource_type: str, resource_name: str
) -> dict[str, Any]:
    matches = [
        resource
        for resource in state.get("resources", [])
        if resource.get("mode") == "managed"
        and resource.get("type") == resource_type
        and resource.get("name") == resource_name
    ]
    if len(matches) != 1:
        raise InventoryError(f"expected one {resource_type}.{resource_name}")
    instances = matches[0].get("instances", [])
    if len(instances) != 1 or not isinstance(instances[0].get("attributes"), dict):
        raise InventoryError(f"invalid state for {resource_type}.{resource_name}")
    return instances[0]["attributes"]


def ssh_cidrs(
    state: dict[str, Any], cloud: str
) -> tuple[list[str], list[str]]:
    if cloud == "yandex":
        attributes = resource_attributes(state, "yandex_vpc_security_group", "vpn")
        ipv4_field = "v4_cidr_blocks"
        ipv6_field = "v6_cidr_blocks"
    else:
        attributes = resource_attributes(state, "aws_security_group", "vpn")
        ipv4_field = "cidr_blocks"
        ipv6_field = "ipv6_cidr_blocks"

    rules = [
        rule
        for rule in attributes.get("ingress", [])
        if rule.get("description") == "SSH from explicitly trusted operator networks"
    ]
    if len(rules) != 1:
        raise InventoryError(f"expected one operator SSH rule in {cloud} state")
    rule = rules[0]
    ipv4 = list(rule.get(ipv4_field) or [])
    ipv6 = list(rule.get(ipv6_field) or [])
    if not ipv4 and not ipv6:
        raise InventoryError(f"empty operator CIDR list in {cloud} state")
    validate_networks(ipv4, 4, f"{cloud} operator IPv4")
    validate_networks(ipv6, 6, f"{cloud} operator IPv6")
    return ipv4, ipv6


def validate_network(value: str, version: int, label: str) -> str:
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as error:
        raise InventoryError(f"invalid {label}") from error
    if network.version != version:
        raise InventoryError(f"wrong address family for {label}")
    return str(network)


def validate_networks(values: list[str], version: int, label: str) -> None:
    for value in values:
        validate_network(value, version, label)


def validate_endpoint(value: Any, label: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise InventoryError(f"invalid {label} endpoint") from error
    if address.version != 4:
        raise InventoryError(f"{label} endpoint must be IPv4")
    return str(address)


def validate_ipv6_address(value: Any, label: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise InventoryError(f"invalid {label} IPv6 address") from error
    if address.version != 6 or not address.is_global:
        raise InventoryError(f"{label} must be a global IPv6 address")
    return str(address)


def rule_port(rule: dict[str, Any]) -> int | None:
    if isinstance(rule.get("port"), int):
        return rule["port"]
    if rule.get("from_port") == rule.get("to_port"):
        return rule.get("from_port")
    return None


def require_multihop_security_groups(
    yandex_state: dict[str, Any], aws_state: dict[str, Any], yc_endpoint: str
) -> None:
    yandex_sg = resource_attributes(
        yandex_state, "yandex_vpc_security_group", "vpn"
    )
    aws_sg = resource_attributes(aws_state, "aws_security_group", "vpn")

    yandex_rules = [
        rule
        for rule in yandex_sg.get("ingress", [])
        if str(rule.get("protocol", "")).upper() == "UDP"
        and rule_port(rule) == MULTIHOP_INGRESS_PORT
        and rule.get("v4_cidr_blocks") == ["0.0.0.0/0"]
    ]
    aws_rules = [
        rule
        for rule in aws_sg.get("ingress", [])
        if str(rule.get("protocol", "")).lower() == "udp"
        and rule_port(rule) == TRANSIT_PORT
        and rule.get("cidr_blocks") == [f"{yc_endpoint}/32"]
        and not (rule.get("ipv6_cidr_blocks") or [])
    ]
    if len(yandex_rules) != 1:
        raise InventoryError("Yandex state does not contain public UDP/1195")
    if len(aws_rules) != 1:
        raise InventoryError(
            "AWS state does not restrict UDP/1196 to the Yandex static IPv4"
        )


def read_endpoint_config() -> dict[str, str]:
    require_protected_file(ENDPOINT_CONFIG)
    values: dict[str, str] = {}
    for line in ENDPOINT_CONFIG.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key in values:
            raise InventoryError("invalid endpoint configuration")
        values[key] = value
    return values


def validate_known_hosts(cloud: str, endpoint: str) -> Path:
    path = REPOSITORY_ROOT / "operator-config" / f"known_hosts.{cloud}-direct"
    require_protected_file(path)
    require_ignored(path)
    records = [
        line.split()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    if len(records) != 1 or len(records[0]) < 3:
        raise InventoryError(f"expected one host-key record for {cloud}-direct")
    hostnames, key_type = records[0][0], records[0][1]
    if endpoint not in hostnames.split(",") or key_type != "ssh-ed25519":
        raise InventoryError(f"host-key record does not match {cloud}-direct")
    return path


def quoted(value: str) -> str:
    return json.dumps(value)


def yaml_list(values: list[str], indentation: int) -> list[str]:
    prefix = " " * indentation
    if not values:
        return [f"{prefix}[]"]
    return [f"{prefix}- {quoted(value)}" for value in values]


def render_host(
    *,
    name: str,
    endpoint: str,
    known_hosts: Path,
    openvpn_port: int,
    vpc_ipv4: str,
    vpn_ipv4: str,
    operator_ipv4: list[str],
    operator_ipv6: list[str],
    enable_ipv6: bool,
    public_ipv6: str | None = None,
    vpc_ipv6: str | None = None,
    vpn_ipv6: str | None = None,
    multihop: dict[str, str] | None = None,
) -> list[str]:
    indent = " " * 8
    lines = [
        f"{indent}{name}:",
        f"{indent}  ansible_host: {quoted(endpoint)}",
        f"{indent}  ansible_user: ubuntu",
        f"{indent}  ansible_ssh_common_args: {quoted(f'-o StrictHostKeyChecking=yes -o UserKnownHostsFile={known_hosts}')}",
        f"{indent}  veilway_dedicated_host_ack: true",
        f"{indent}  veilway_mode: {name}",
        f"{indent}  veilway_openvpn_port: {openvpn_port}",
        f"{indent}  veilway_vpc_ipv4_cidr: {quoted(vpc_ipv4)}",
    ]
    if vpc_ipv6 is not None:
        lines.append(f"{indent}  veilway_vpc_ipv6_cidr: {quoted(vpc_ipv6)}")
    lines.append(f"{indent}  veilway_vpn_ipv4_cidr: {quoted(vpn_ipv4)}")
    if vpn_ipv6 is not None:
        lines.append(f"{indent}  veilway_vpn_ipv6_cidr: {quoted(vpn_ipv6)}")
    if public_ipv6 is not None:
        lines.append(f"{indent}  veilway_public_ipv6: {quoted(public_ipv6)}")
    lines.append(f"{indent}  veilway_operator_ipv4_cidrs:")
    lines.extend(yaml_list(operator_ipv4, 12))
    lines.append(f"{indent}  veilway_operator_ipv6_cidrs:")
    lines.extend(yaml_list(operator_ipv6, 12))
    lines.append(
        f"{indent}  veilway_enable_ipv6: {'true' if enable_ipv6 else 'false'}"
    )
    lines.append(
        f"{indent}  veilway_multihop_enabled: "
        f"{'true' if multihop is not None else 'false'}"
    )
    if multihop is not None:
        lines.extend(
            [
                f"{indent}  veilway_multihop_ingress_port: {MULTIHOP_INGRESS_PORT}",
                f"{indent}  veilway_transit_port: {TRANSIT_PORT}",
                f"{indent}  veilway_multihop_ipv4_cidr: {quoted(multihop['ipv4'])}",
                f"{indent}  veilway_multihop_ipv6_cidr: {quoted(multihop['ipv6'])}",
                f"{indent}  veilway_transit_ipv4_cidr: {quoted(multihop['transit_ipv4'])}",
                f"{indent}  veilway_transit_ipv6_cidr: {quoted(multihop['transit_ipv6'])}",
                f"{indent}  veilway_yc_public_ipv4: {quoted(multihop['yc_endpoint'])}",
                f"{indent}  veilway_aws_public_ipv4: {quoted(multihop['aws_endpoint'])}",
            ]
        )
    return lines


def main() -> None:
    arguments = os.sys.argv[1:]
    if len(arguments) != len(set(arguments)) or any(
        argument not in {"--replace", "--enable-multihop"}
        for argument in arguments
    ):
        raise InventoryError(
            "usage: render-inventory.py [--replace] [--enable-multihop]"
        )
    replace = "--replace" in arguments
    enable_multihop = "--enable-multihop" in arguments

    if INVENTORY_PATH.exists() or INVENTORY_PATH.is_symlink():
        if not replace:
            raise InventoryError(
                "inventory.yml already exists; use --replace after reviewing the change"
            )
        require_protected_file(INVENTORY_PATH)
        require_ignored(INVENTORY_PATH)
    require_ignored(INVENTORY_PATH)

    yandex_state = load_state("yandex")
    aws_state = load_state("aws")
    endpoints = read_endpoint_config()

    yc_endpoint = validate_endpoint(output_value(yandex_state, "public_ipv4"), "yc")
    aws_endpoint = validate_endpoint(output_value(aws_state, "public_ipv4"), "aws")
    if endpoints != {
        "yc_direct_endpoint": yc_endpoint,
        "aws_direct_endpoint": aws_endpoint,
    }:
        raise InventoryError("endpoint configuration does not match Terraform state")

    multihop: dict[str, str] | None = None
    if enable_multihop:
        require_multihop_security_groups(yandex_state, aws_state, yc_endpoint)
        yc_multihop_ipv4 = validate_network(
            output_value(yandex_state, "multihop_ipv4_cidr"),
            4,
            "Yandex multi-hop VPN",
        )
        aws_multihop_ipv4 = validate_network(
            output_value(aws_state, "multihop_ipv4_cidr"),
            4,
            "AWS multi-hop VPN",
        )
        yc_transit_ipv4 = validate_network(
            output_value(yandex_state, "transit_ipv4_cidr"),
            4,
            "Yandex transit VPN",
        )
        aws_transit_ipv4 = validate_network(
            output_value(aws_state, "transit_ipv4_cidr"),
            4,
            "AWS transit VPN",
        )
        if yc_multihop_ipv4 != aws_multihop_ipv4:
            raise InventoryError("multi-hop IPv4 outputs differ between clouds")
        if yc_transit_ipv4 != aws_transit_ipv4:
            raise InventoryError("transit IPv4 outputs differ between clouds")
        multihop = {
            "ipv4": yc_multihop_ipv4,
            "ipv6": validate_network(
                output_value(aws_state, "multihop_ipv6_cidr"),
                6,
                "AWS multi-hop VPN IPv6",
            ),
            "transit_ipv4": yc_transit_ipv4,
            "transit_ipv6": validate_network(
                output_value(aws_state, "transit_ipv6_cidr"),
                6,
                "AWS transit VPN IPv6",
            ),
            "yc_endpoint": yc_endpoint,
            "aws_endpoint": aws_endpoint,
        }

    yc_alias = output_value(yandex_state, "ansible_host_alias")
    aws_alias = output_value(aws_state, "ansible_host_alias")
    if yc_alias != {"name": "yc-direct", "user": "ubuntu"}:
        raise InventoryError("unexpected Yandex Ansible alias")
    if aws_alias != {"name": "aws-direct", "user": "ubuntu"}:
        raise InventoryError("unexpected AWS Ansible alias")

    yc_operator_ipv4, yc_operator_ipv6 = ssh_cidrs(yandex_state, "yandex")
    aws_operator_ipv4, aws_operator_ipv6 = ssh_cidrs(aws_state, "aws")
    yc_known_hosts = validate_known_hosts("yc", yc_endpoint)
    aws_known_hosts = validate_known_hosts("aws", aws_endpoint)

    lines = ["---", "all:", "  children:", "    direct_vpn:", "      hosts:"]
    lines.extend(
        render_host(
            name="yc-direct",
            endpoint=yc_endpoint,
            known_hosts=yc_known_hosts,
            openvpn_port=OPENVPN_PORTS["yc-direct"],
            vpc_ipv4=validate_network(
                output_value(yandex_state, "vpc_cidr"), 4, "Yandex VPC"
            ),
            vpn_ipv4=validate_network(
                output_value(yandex_state, "vpn_ipv4_cidr"), 4, "Yandex VPN"
            ),
            operator_ipv4=yc_operator_ipv4,
            operator_ipv6=yc_operator_ipv6,
            enable_ipv6=False,
            multihop=multihop,
        )
    )
    lines.append("")
    lines.extend(
        render_host(
            name="aws-direct",
            endpoint=aws_endpoint,
            known_hosts=aws_known_hosts,
            openvpn_port=OPENVPN_PORTS["aws-direct"],
            vpc_ipv4=validate_network(
                output_value(aws_state, "vpc_cidr"), 4, "AWS VPC"
            ),
            vpc_ipv6=validate_network(
                output_value(aws_state, "vpc_ipv6_cidr"), 6, "AWS VPC IPv6"
            ),
            vpn_ipv4=validate_network(
                output_value(aws_state, "vpn_ipv4_cidr"), 4, "AWS VPN"
            ),
            vpn_ipv6=validate_network(
                output_value(aws_state, "vpn_ipv6_cidr"), 6, "AWS VPN IPv6"
            ),
            public_ipv6=validate_ipv6_address(
                output_value(aws_state, "public_ipv6"), "AWS ENI"
            ),
            operator_ipv4=aws_operator_ipv4,
            operator_ipv6=aws_operator_ipv6,
            enable_ipv6=True,
            multihop=multihop,
        )
    )
    inventory_text = "\n".join(lines) + "\n"

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".inventory.", dir=INVENTORY_PATH.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(inventory_text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, INVENTORY_PATH)
    finally:
        temporary_path.unlink(missing_ok=True)

    print("Ansible inventory: generated from protected Terraform state")
    print("deploy/inventory.yml: protected and ignored")


if __name__ == "__main__":
    try:
        main()
    except (InventoryError, OSError, subprocess.SubprocessError) as error:
        print(f"render-inventory.py: {error}", file=os.sys.stderr)
        raise SystemExit(1)
