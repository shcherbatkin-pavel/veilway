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
    if set(services) != {"api", "db", "web", "pki"}:
        fail("steady state must contain exactly api, db, web and pki")
    if any(services[name].get("ports") for name in ("api", "db", "pki")):
        fail("API, PostgreSQL and PKI must not publish ports")

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
        for name in (
            "POSTGRES_PASSWORD", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
            "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "ADMIN_GOOGLE_EMAIL",
            "VEILWAY_GOOGLE_CLIENT_ID", "VEILWAY_GOOGLE_CLIENT_SECRET", "VEILWAY_ADMIN_GOOGLE_EMAIL",
        )
    ):
        fail("runtime secret values must not be Docker environment variables")
    if set(document.get("secrets", {})) != {
        "aws_access_key_id",
        "aws_secret_access_key",
        "postgres_password",
        "google_client_id",
        "google_client_secret",
        "admin_google_email",
        "pki_ca_passphrase",
        "pki_endpoints",
    }:
        fail("unexpected runtime secret set")

    pki = services["pki"]
    if pki.get("network_mode") != "none" or pki.get("networks") or not pki.get("read_only"):
        fail("PKI must have no network and a read-only root filesystem")
    if set(pki.get("cap_drop", [])) != {"ALL"} or set(pki.get("cap_add", [])) != {"SETUID", "SETGID"}:
        fail("PKI may only drop privileges, without networking capabilities")
    if not any("no-new-privileges" in option for option in pki.get("security_opt", [])):
        fail("PKI must prevent privilege escalation")
    allowed_mounts = {"pki": {"/var/lib/veilway-pki", "/run/veilway-pki"},
                      "api": {"/run/veilway", "/run/veilway-pki"},
                      "web": {"/run/veilway", "/data", "/config"},
                      "db": {"/var/lib/postgresql/data"}}
    for name, service in services.items():
        volumes = service.get("volumes", [])
        if {mount["target"] for mount in volumes} != allowed_mounts[name] or len(volumes) != len(allowed_mounts[name]):
            fail("unexpected bind mount or Docker socket")
        if any("docker.sock" in mount.get("source", "") for mount in volumes):
            fail("Docker socket must never be mounted")
    pki_volumes = {mount["target"]: mount["source"] for mount in pki["volumes"]}
    for name in ("api", "web", "db"):
        if any(mount["source"] == pki_volumes["/var/lib/veilway-pki"] or
               pki_volumes["/var/lib/veilway-pki"].startswith(mount["source"].rstrip("/") + "/")
               for mount in services[name]["volumes"]):
            fail("CA storage must be exclusive to PKI")
    api_socket = next(mount for mount in services["api"]["volumes"] if mount["target"] == "/run/veilway-pki")
    if not api_socket.get("read_only") or api_socket["source"] != pki_volumes["/run/veilway-pki"]:
        fail("API must share only the read-only PKI socket directory")
    expected_secrets = {"pki": {"pki_ca_passphrase", "pki_endpoints"}, "web": set(),
                        "db": {"postgres_password"}, "api": {"aws_access_key_id", "aws_secret_access_key", "postgres_password",
                            "google_client_id", "google_client_secret", "admin_google_email"}}
    for name, expected in expected_secrets.items():
        if {item["source"] for item in services[name].get("secrets", [])} != expected:
            fail("runtime secrets must stay within their service boundary")
    if any("PASSPHRASE" in name or "PASSWORD" in name for name in pki.get("environment", {})):
        fail("PKI passphrase must be a runtime file, not an environment value")
    print("control Compose: four services, only TCP/80+443; PKI has no network and exclusive CA storage")


if __name__ == "__main__":
    main()
