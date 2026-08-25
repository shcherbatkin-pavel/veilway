#!/usr/bin/env python3
"""Exercise the operator PKI lifecycle entirely in a temporary repository."""

import hashlib
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile

try:
    import pexpect
except ImportError:
    print("Error: pki-smoke.py requires the local Python pexpect package.", file=sys.stderr)
    raise SystemExit(2)


SOURCE_ROOT = Path(__file__).resolve().parent.parent
PROFILE_NAMES = (
    "ubuntu-yc-direct",
    "iphone-yc-direct",
    "ubuntu-aws-direct",
    "iphone-aws-direct",
)


def require_command(name):
    if shutil.which(name) is None:
        raise RuntimeError(f"required command is unavailable: {name}")


def prepare_test_repository(root):
    for relative in (
        "scripts/veilway-pki",
        "pki/openssl-ca.cnf",
        "operator-config/endpoints.conf.example",
    ):
        source = SOURCE_ROOT / relative
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    (root / "operator-config/endpoints.conf").write_text(
        "yc_direct_endpoint=192.0.2.10\n"
        "aws_direct_endpoint=192.0.2.20\n",
        encoding="utf-8",
    )
    os.chmod(root / "operator-config/endpoints.conf", 0o600)


def run_cli(root, passphrase, *arguments):
    command = str(root / "scripts/veilway-pki")
    child = pexpect.spawn(
        command,
        list(arguments),
        cwd=str(root),
        encoding="utf-8",
        timeout=180,
    )
    prompt_patterns = (
        r"Enter PEM pass phrase:",
        r"Verifying - Enter PEM pass phrase:",
        r"Enter pass phrase for .*ca\.key:",
    )
    while True:
        matched = child.expect([pexpect.EOF, pexpect.TIMEOUT, *prompt_patterns])
        if matched == 0:
            break
        if matched == 1:
            child.close(force=True)
            raise RuntimeError(f"PKI command timed out: {' '.join(arguments)}")
        child.sendline(passphrase)

    child.close()
    if child.exitstatus != 0:
        output = (child.before or "").strip()
        raise RuntimeError(
            f"PKI command failed ({child.exitstatus}): {' '.join(arguments)}\n{output}"
        )


def inline_section(profile_text, tag):
    match = re.search(
        rf"<{re.escape(tag)}>\s*(.+?)\s*</{re.escape(tag)}>",
        profile_text,
        flags=re.DOTALL,
    )
    if match is None:
        raise RuntimeError(f"profile is missing <{tag}>")
    return match.group(1).encode("utf-8")


def validate_profiles(root):
    certificate_hashes = set()
    tls_key_hashes = set()
    for profile_name in PROFILE_NAMES:
        profile_path = root / "client-profiles" / f"{profile_name}.ovpn"
        if stat.S_IMODE(profile_path.stat().st_mode) != 0o600:
            raise RuntimeError(f"profile mode is not 0600: {profile_path.name}")
        profile_text = profile_path.read_text(encoding="utf-8")
        certificate_hashes.add(
            hashlib.sha256(inline_section(profile_text, "cert")).digest()
        )
        tls_key_hashes.add(
            hashlib.sha256(inline_section(profile_text, "tls-crypt-v2")).digest()
        )

    if len(certificate_hashes) != len(PROFILE_NAMES):
        raise RuntimeError("client certificates are not unique")
    if len(tls_key_hashes) != len(PROFILE_NAMES):
        raise RuntimeError("tls-crypt-v2 client keys are not unique")


def validate_revocation(root):
    command = [
        "openssl",
        "verify",
        "-crl_check",
        "-purpose",
        "sslclient",
        "-CAfile",
        str(root / "secrets/pki/ca/ca.crt"),
        "-CRLfile",
        str(root / "secrets/pki/crl.pem"),
        str(root / "secrets/pki/clients/ubuntu-yc-direct/client.crt"),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    combined_output = f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode == 0 or "certificate revoked" not in combined_output:
        raise RuntimeError("revoked client certificate was not rejected by the CRL")


def main():
    require_command("docker")
    require_command("openssl")
    subprocess.run(
        ["docker", "image", "inspect", "veilway/openvpn:2.6-ubuntu24.04"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )

    passphrase = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix="veilway-pki-smoke.", dir="/tmp") as temporary:
        root = Path(temporary)
        prepare_test_repository(root)
        run_cli(root, passphrase, "init")
        for mode in ("yc-direct", "aws-direct"):
            run_cli(root, passphrase, "server", "create", "--mode", mode)
        for profile_name in PROFILE_NAMES:
            device, mode = profile_name.split("-", maxsplit=1)
            run_cli(
                root,
                passphrase,
                "profile",
                "create",
                "--device",
                device,
                "--mode",
                mode,
            )
        validate_profiles(root)
        run_cli(
            root,
            passphrase,
            "profile",
            "revoke",
            "--name",
            "ubuntu-yc-direct",
        )
        validate_revocation(root)

    print("pki-smoke.py: temporary four-profile lifecycle and revocation passed")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
