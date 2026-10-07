#!/usr/bin/env python3

"""Validate local control-plane inputs and run one explicitly selected playbook."""

from __future__ import annotations

import argparse
import json
import ipaddress
import os
import subprocess
import sys
from pathlib import Path

from lib.operator_validation import protected_file_mode


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
ALLOWED_KEYS = {
    "PKI_CA_PASSPHRASE",
    "PKI_YC_ENDPOINT",
    "PKI_AWS_ENDPOINT",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "ADMIN_GOOGLE_EMAIL",
    "POSTGRES_PASSWORD",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_REGION",
    "AWS_DIRECT_INSTANCE_ID",
    "YC_REGION",
    "YC_DIRECT_INSTANCE_ID",
    "CRL_AWS_DIRECT_TOKEN",
    "CRL_YC_DIRECT_TOKEN",
    "HEARTBEAT_AWS_DIRECT_TOKEN",
    "HEARTBEAT_YC_DIRECT_TOKEN",
}
MINIMUM_LENGTHS = {
    # Import an existing encrypted CA using its unchanged, nonempty password.
    "PKI_CA_PASSPHRASE": 1,
    "PKI_YC_ENDPOINT": 7,
    "PKI_AWS_ENDPOINT": 7,
    "GOOGLE_CLIENT_ID": 10,
    "GOOGLE_CLIENT_SECRET": 16,
    "ADMIN_GOOGLE_EMAIL": 5,
    "POSTGRES_PASSWORD": 20,
    "AWS_ACCESS_KEY_ID": 16,
    "AWS_SECRET_ACCESS_KEY": 32,
    "AWS_REGION": 3,
    "AWS_DIRECT_INSTANCE_ID": 6,
    "YC_REGION": 3,
    "YC_DIRECT_INSTANCE_ID": 6,
    "CRL_AWS_DIRECT_TOKEN": 32,
    "CRL_YC_DIRECT_TOKEN": 32,
    "HEARTBEAT_AWS_DIRECT_TOKEN": 32,
    "HEARTBEAT_YC_DIRECT_TOKEN": 32,
}


def parse_value(raw: str, line_number: int) -> str:
    value = raw.strip()
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid quoted value on line {line_number}") from error
        if not isinstance(parsed, str):
            raise ValueError(f"value on line {line_number} must be a string")
        return parsed
    return value


def load_environment(path: Path) -> dict[str, str]:
    mode = protected_file_mode(path)
    if mode != 0o600:
        raise ValueError(f"{path} must have mode 0600, current mode is {mode:04o}")
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", "--", str(path)],
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    if ignored.returncode != 0:
        raise ValueError(f"{path} must be ignored by Git")

    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line or line.startswith("export "):
            raise ValueError(f"invalid assignment on line {line_number}")
        key, raw_value = line.split("=", 1)
        if key not in ALLOWED_KEYS:
            raise ValueError(f"unsupported key on line {line_number}")
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        value = parse_value(raw_value, line_number)
        if value.startswith("replace-with-"):
            raise ValueError(f"unreplaced placeholder on line {line_number}")
        if "\n" in value or "\r" in value or "\x00" in value:
            raise ValueError(f"invalid characters in {key}")
        result[key] = value

    missing = ALLOWED_KEYS - set(result)
    if missing:
        raise ValueError("missing required keys: " + ", ".join(sorted(missing)))
    for key, minimum in MINIMUM_LENGTHS.items():
        if len(result[key]) < minimum:
            raise ValueError(f"{key} must contain at least {minimum} characters")
        if len(result[key]) > 1024:
            raise ValueError(f"{key} exceeds the maximum length")
    agent_tokens = [result[key] for key in ("CRL_AWS_DIRECT_TOKEN", "CRL_YC_DIRECT_TOKEN", "HEARTBEAT_AWS_DIRECT_TOKEN", "HEARTBEAT_YC_DIRECT_TOKEN")]
    if len(set(agent_tokens)) != 4 or any(len(token) > 256 or any(ord(c) < 33 or ord(c) > 126 for c in token) for token in agent_tokens):
        raise ValueError("agent tokens must be distinct and contain 32–256 printable characters")
    email = result["ADMIN_GOOGLE_EMAIL"]
    for key in ("PKI_YC_ENDPOINT", "PKI_AWS_ENDPOINT"):
        try:
            if str(ipaddress.IPv4Address(result[key])) != result[key]:
                raise ValueError
        except ValueError:
            raise ValueError(f"{key} must be a plain IPv4 address") from None
    if len(email) > 320 or email.count("@") != 1 or any(c.isspace() for c in email):
        raise ValueError("ADMIN_GOOGLE_EMAIL must be an email address")
    local, domain = email.split("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("ADMIN_GOOGLE_EMAIL must be an email address")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("component", choices=("web", "heartbeat", "crl"))
    parser.add_argument("--env-file", type=Path, default=REPOSITORY_ROOT / ".env")
    parser.add_argument(
        "--inventory",
        type=Path,
        default=REPOSITORY_ROOT / "deploy/control-inventory.yml",
    )
    parser.add_argument("--enable-crl-agent", action="store_true", help="enable CRL polling after an approved directory mount cutover")
    parser.add_argument("--limit", choices=("aws-direct", "yc-direct"))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the selected remote deployment; otherwise only validate inputs",
    )
    arguments = parser.parse_args()

    try:
        values = load_environment(arguments.env_file.resolve())
    except (OSError, ValueError) as error:
        print(f"deployment input rejected: {error}", file=sys.stderr)
        return 2
    if not arguments.inventory.is_file():
        print("deployment input rejected: inventory does not exist", file=sys.stderr)
        return 2
    if arguments.enable_crl_agent and arguments.component != "crl":
        print("--enable-crl-agent requires component crl", file=sys.stderr)
        return 2
    if arguments.component == "web" and arguments.limit:
        print("--limit is only valid for node agent deployments", file=sys.stderr)
        return 2

    targets = arguments.limit or ("control_web" if arguments.component == "web" else "direct_vpn")
    print(f"validated {arguments.component} deployment for inventory target {targets}")
    if not arguments.apply:
        print("validation only; no host was contacted (add --apply to deploy)")
        return 0

    playbook = {"web": "control-web.yml", "heartbeat": "heartbeat.yml", "crl": "crl-agents.yml"}[arguments.component]
    command = [
        "ansible-playbook",
        "--inventory",
        str(arguments.inventory.resolve()),
        str(REPOSITORY_ROOT / "deploy" / playbook),
    ]
    if arguments.enable_crl_agent:
        command.extend(["--extra-vars", '{"veilway_crl_agent_enable":true,"veilway_crl_agent_mount_ack":true}'])
    if arguments.limit:
        command.extend(["--limit", arguments.limit])
    environment = os.environ.copy()
    environment["ANSIBLE_CONFIG"] = str(REPOSITORY_ROOT / "deploy/ansible.cfg")
    for key, value in values.items():
        environment[f"VEILWAY_DEPLOY_{key}"] = value
    return subprocess.run(command, cwd=REPOSITORY_ROOT, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
