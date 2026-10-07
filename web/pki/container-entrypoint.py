#!/usr/bin/env python3
"""Seal the root-only CA passphrase before permanently dropping privileges."""
import fcntl
import os
from pathlib import Path
import sys


def main():
    if os.geteuid() != 0 or sys.argv[1:] not in (["serve"],) and not (
        len(sys.argv) == 4 and sys.argv[1] in {"import-ca", "import-profiles"} and sys.argv[2] == "--source"
    ):
        raise RuntimeError("invalid PKI entrypoint invocation")
    environment = os.environ.copy()
    for variable, filename in (("VEILWAY_PKI_PASSPHRASE_FILE", "pki_ca_passphrase"),
                               ("VEILWAY_PKI_ENDPOINTS_FILE", "pki_endpoints")):
        fd = os.open(Path("/run/secrets") / filename, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            value = os.read(fd, 65537)
        finally:
            os.close(fd)
        if not value.strip() or len(value) > 65536:
            raise RuntimeError("invalid PKI runtime input")
        descriptor = os.memfd_create("pki-runtime-input", flags=os.MFD_ALLOW_SEALING)
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, value)
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE)
        os.set_inheritable(descriptor, True)
        environment[variable] = f"/proc/self/fd/{descriptor}"
    os.setgroups([10003])
    os.setgid(10002)
    os.setuid(10002)
    os.umask(0o077)
    os.execve("/usr/local/bin/python", ["python", "-m", "veilway_pki", *sys.argv[1:]], environment)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("PKI startup failed.", file=sys.stderr)
        raise SystemExit(1)
