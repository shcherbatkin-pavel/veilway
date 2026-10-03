"""Shared pure validation for local operator inputs."""

import ipaddress
import stat
from pathlib import Path
from typing import Any


def protected_file_mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def ipv4_host_cidrs(values: list[str], count: int) -> bool:
    try:
        return len(values) == count and all(
            (network := ipaddress.ip_network(value, strict=True)).version == 4
            and network.prefixlen == 32 for value in values
        )
    except ValueError:
        return False


def rule_port(rule: dict[str, Any]) -> int | None:
    if isinstance(rule.get("port"), int):
        return rule["port"]
    if rule.get("from_port") == rule.get("to_port"):
        return rule.get("from_port")
    return None
