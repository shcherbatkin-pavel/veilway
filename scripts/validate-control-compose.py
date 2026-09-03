#!/usr/bin/env python3

"""Reject accidental expansion of the public control-plane container surface."""

from __future__ import annotations

import json
import sys


def fail(message: str) -> None:
    raise SystemExit(f"control Compose validation failed: {message}")


def main() -> None:
    document = json.load(sys.stdin)
    services = document.get("services", {})
    if set(services) != {"api", "db", "web"}:
        fail("steady state must contain exactly api, db and web")
    if services["api"].get("ports") or services["db"].get("ports"):
        fail("API and PostgreSQL must not publish ports")

    ports = services["web"].get("ports", [])
    exposed = {
        (int(item["target"]), int(item["published"]), item.get("protocol", "tcp"))
        for item in ports
    }
    if exposed != {(80, 80, "tcp"), (443, 443, "tcp")}:
        fail("web must publish only TCP/80 and TCP/443")
    if not document.get("networks", {}).get("data", {}).get("internal"):
        fail("database network must be internal")
    if not services["api"].get("read_only") or not services["web"].get("read_only"):
        fail("API and web root filesystems must be read-only")

    api_environment = services["api"].get("environment", {})
    if any(
        name in api_environment
        for name in ("POSTGRES_PASSWORD", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
    ):
        fail("runtime secret values must not be Docker environment variables")
    if set(document.get("secrets", {})) != {
        "aws_access_key_id",
        "aws_secret_access_key",
        "postgres_password",
    }:
        fail("unexpected runtime secret set")

    print("control Compose surface is restricted to three services and TCP/80+443")


if __name__ == "__main__":
    main()
