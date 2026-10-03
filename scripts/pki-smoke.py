#!/usr/bin/env python3
"""Exercise the operator PKI lifecycle entirely in a temporary repository."""

import hashlib
import importlib.util
import json
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
    "ubuntu-yc-aws-multihop",
    "iphone-yc-aws-multihop",
    "test-windows-aws-direct",
    "test-iphone-yc-aws-multihop",
)
SERVER_MODES = (
    "yc-direct",
    "aws-direct",
    "yc-multihop-ingress",
    "aws-transit",
)


def require_command(name):
    if shutil.which(name) is None:
        raise RuntimeError(f"required command is unavailable: {name}")


def prepare_test_repository(root):
    for relative in (
        ".gitignore",
        "scripts/veilway-pki",
        "scripts/pki-expiry.py",
        "scripts/verify-client-profiles",
        "pki/openssl-ca.cnf",
        "operator-config/endpoints.conf.example",
    ):
        source = SOURCE_ROOT / relative
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    shutil.copytree(SOURCE_ROOT / "scripts/lib/pki", root / "scripts/lib/pki")

    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)

    (root / "operator-config/endpoints.conf").write_text(
        "yc_direct_endpoint=192.0.2.10\n"
        "aws_direct_endpoint=192.0.2.20\n",
        encoding="utf-8",
    )
    os.chmod(root / "operator-config/endpoints.conf", 0o600)

    for cloud, endpoint in (
        ("yandex", "192.0.2.10"),
        ("aws", "192.0.2.20"),
    ):
        state_path = root / "infra" / cloud / "terraform.tfstate"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"outputs": {"public_ipv4": {"value": endpoint}}}),
            encoding="utf-8",
        )
        os.chmod(state_path, 0o600)


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


def run_cli_expect_failure(root, *arguments):
    result = subprocess.run(
        [str(root / "scripts/veilway-pki"), *arguments],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        raise RuntimeError(f"PKI command unexpectedly passed: {' '.join(arguments)}")


def split_profile_name(profile_name):
    for mode in ("yc-aws-multihop", "yc-direct", "aws-direct"):
        suffix = f"-{mode}"
        if profile_name.endswith(suffix):
            return profile_name[: -len(suffix)], mode
    raise RuntimeError(f"profile name has no known mode: {profile_name}")


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
        expected_port = 1195 if profile_name.endswith("yc-aws-multihop") else 1194
        endpoint = "192.0.2.20" if profile_name.endswith("aws-direct") else "192.0.2.10"
        if profile_text.count(f"remote {endpoint} {expected_port}\n") != 1:
            raise RuntimeError(f"profile remote is invalid: {profile_path.name}")
        _, profile_mode = split_profile_name(profile_name)
        expected_server = (
            "yc-multihop-ingress"
            if profile_mode == "yc-aws-multihop"
            else profile_mode
        )
        if profile_text.count(f"verify-x509-name {expected_server} name\n") != 1:
            raise RuntimeError(f"profile server identity is invalid: {profile_path.name}")
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


def validate_transit_identity(root):
    transit_dir = root / "secrets/pki/endpoints/yc-transit"
    certificate = transit_dir / "client.crt"
    private_key = transit_dir / "client.key"
    tls_key = transit_dir / "tls-crypt-v2-client.key"
    expected_modes = {
        certificate: 0o644,
        private_key: 0o600,
        tls_key: 0o600,
    }
    for path, expected_mode in expected_modes.items():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"missing transit identity file: {path.name}")
        if stat.S_IMODE(path.stat().st_mode) != expected_mode:
            raise RuntimeError(f"invalid transit identity mode: {path.name}")

    verify = subprocess.run(
        [
            "openssl",
            "verify",
            "-purpose",
            "sslclient",
            "-CAfile",
            str(root / "secrets/pki/ca/ca.crt"),
            str(certificate),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if verify.returncode != 0:
        raise RuntimeError("transit client certificate validation failed")

    certificate_public_key = subprocess.run(
        ["openssl", "x509", "-in", str(certificate), "-pubkey", "-noout"],
        capture_output=True,
        check=True,
    ).stdout
    private_public_key = subprocess.run(
        ["openssl", "pkey", "-in", str(private_key), "-pubout"],
        capture_output=True,
        check=True,
    ).stdout
    if certificate_public_key != private_public_key:
        raise RuntimeError("transit certificate and private key do not match")


def run_profile_validator(root, *arguments):
    result = subprocess.run(
        [str(root / "scripts/verify-client-profiles"), *arguments],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "profile validator failed"
            + (f": {result.stderr.strip()}" if result.stderr.strip() else "")
        )


def validate_revocation(root, profile_name):
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
        str(root / "secrets/pki/clients" / profile_name / "client.crt"),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    combined_output = f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode == 0 or "certificate revoked" not in combined_output:
        raise RuntimeError("revoked client certificate was not rejected by the CRL")


def main():
    require_command("git")
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
        for mode in SERVER_MODES:
            run_cli(root, passphrase, "server", "create", "--mode", mode)
        run_cli(root, passphrase, "transit", "create")
        from datetime import datetime, timedelta, timezone
        expiry_spec = importlib.util.spec_from_file_location("expiry", root / "scripts/pki-expiry.py")
        expiry_module = importlib.util.module_from_spec(expiry_spec)
        expiry_spec.loader.exec_module(expiry_module)
        for index, options in enumerate((
            (), ("--valid-for", "1mo"), ("--valid-for", "3mo"),
            ("--valid-for", "6mo"), ("--valid-for", "12mo"),
            ("--valid-for", "10m"),
            ("--expires-at", (datetime.now(timezone(timedelta(hours=3))) + timedelta(minutes=10)).replace(microsecond=0).isoformat()),
        )):
            before_issue = datetime.now(timezone.utc)
            run_cli(root, passphrase, "profile", "create", "--device", f"expiry-{index}", "--mode", "yc-direct", *options)
            after_issue = datetime.now(timezone.utc)
            cert = root / "secrets/pki/clients" / f"expiry-{index}-yc-direct/client.crt"
            dates = subprocess.check_output(["openssl", "x509", "-in", str(cert), "-noout", "-dates"], text=True).splitlines()
            start, end = [datetime.strptime(line.split("=", 1)[1], "%b %d %H:%M:%S %Y GMT").replace(tzinfo=timezone.utc) for line in dates]
            kwargs = {options[0][2:].replace("-", "_"): options[1]} if options else {}
            if not expiry_module.calculate(before_issue, **kwargs) <= end <= expiry_module.calculate(after_issue, **kwargs):
                raise RuntimeError("issued expiry does not match requested lifetime")
            if index == 0 and abs((end - start).total_seconds() - 365 * 86400) > 10:
                raise RuntimeError("default expiry changed")
            for instant, accepted in ((end - timedelta(seconds=1), True), (end + timedelta(seconds=1), False)):
                result = subprocess.run(["openssl", "verify", "-attime", str(int(instant.timestamp())), "-CAfile", str(root / "secrets/pki/ca/ca.crt"), str(cert)], capture_output=True, text=True)
                if (result.returncode == 0) != accepted or (not accepted and "certificate has expired" not in result.stdout + result.stderr):
                    raise RuntimeError("certificate expiry verification failed")
        ca = root / "secrets/pki/ca"
        before = {name: (ca / name).read_bytes() for name in ("index.txt", "serial")}
        for options in (("--valid-for", "0m"), ("--valid-for", "999mo"), ("--expires-at", "2000-01-01T00:00:00Z"), ("--valid-for", "5m", "--expires-at", "2000-01-01T00:00:00Z")):
            run_cli_expect_failure(root, "profile", "create", "--device", "invalid-expiry", "--mode", "yc-direct", *options)
            if any((ca / name).read_bytes() != value for name, value in before.items()):
                raise RuntimeError("invalid expiry changed CA database")
            if (root / "secrets/pki/clients/invalid-expiry-yc-direct").exists():
                raise RuntimeError("invalid expiry created client material")
        for invalid_device in (
            "",
            "../escape",
            "Uppercase",
            "-leading",
            "trailing-",
            "double--hyphen",
            "a" * 49,
        ):
            run_cli_expect_failure(
                root,
                "profile",
                "create",
                "--device",
                invalid_device,
                "--mode",
                "yc-direct",
            )
        for profile_name in PROFILE_NAMES:
            device, mode = split_profile_name(profile_name)
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
        for profile_name in PROFILE_NAMES:
            if not profile_name.endswith("aws-direct"):
                continue
            profile_path = root / "client-profiles" / f"{profile_name}.ovpn"
            profile_text = profile_path.read_text(encoding="utf-8")
            profile_path.write_text(
                profile_text.replace(
                    "remote 192.0.2.20 1194\n",
                    "remote 192.0.2.20 443\n",
                    1,
                ),
                encoding="utf-8",
            )
            os.chmod(profile_path, 0o600)
        run_cli(root, passphrase, "profile", "update-remote", "--mode", "aws-direct")
        validate_profiles(root)
        validate_transit_identity(root)
        run_profile_validator(root, "--direct-only")
        run_profile_validator(root)
        run_cli(
            root,
            passphrase,
            "profile",
            "revoke",
            "--name",
            "test-iphone-yc-aws-multihop",
        )
        validate_revocation(root, "test-iphone-yc-aws-multihop")

    print(
        "pki-smoke.py: temporary eight-profile, transit, and revocation lifecycle passed"
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
