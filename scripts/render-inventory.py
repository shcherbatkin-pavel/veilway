#!/usr/bin/env python3
"""Render the ignored Direct VPN inventory from protected local state."""

from __future__ import annotations

import json
import os
from pathlib import Path

import subprocess
import tempfile
from typing import Any

from lib.operator_validation import protected_file_mode
from lib.inventory import InventoryError, output_value, validate_endpoint, validate_inventory, render_inventory


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
INVENTORY_PATH = REPOSITORY_ROOT / "deploy" / "inventory.yml"
ENDPOINT_CONFIG = REPOSITORY_ROOT / "operator-config" / "endpoints.conf"


def require_protected_file(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise InventoryError(f"missing regular protected file: {path.name}")
    if protected_file_mode(path) != 0o600:
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


def write_inventory(path: Path, inventory_text: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".inventory.", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(inventory_text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)



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

    known_hosts = {
        "yc": validate_known_hosts("yc", validate_endpoint(output_value(yandex_state, "public_ipv4"), "yc")),
        "aws": validate_known_hosts("aws", validate_endpoint(output_value(aws_state, "public_ipv4"), "aws")),
    }
    hosts = validate_inventory(yandex_state, aws_state, endpoints, known_hosts, enable_multihop=enable_multihop)
    inventory_text = render_inventory(hosts)
    write_inventory(INVENTORY_PATH, inventory_text)

    print("Ansible inventory: generated from protected Terraform state")
    print("deploy/inventory.yml: protected and ignored")


if __name__ == "__main__":
    try:
        main()
    except (InventoryError, OSError, subprocess.SubprocessError) as error:
        print(f"render-inventory.py: {error}", file=os.sys.stderr)
        raise SystemExit(1)
