"""Allowlisted OpenSSL/OpenVPN subprocesses; no shell or public arguments."""

import fcntl
import os
from pathlib import Path
import subprocess

from .errors import PkiError


def run_command(args: list[str], cwd: Path, *, passphrase: bytes | None = None) -> bytes:
    # Call sites use fixed executable/subcommands and validated identifiers.
    # Never accept executable names, config text or shell fragments via RPC.
    if args[0] not in {"/usr/bin/openssl", "/usr/sbin/openvpn"}:
        raise PkiError("operation_failed")
    descriptor = None
    try:
        if passphrase is not None:
            descriptor = os.memfd_create("pki-passphrase", flags=os.MFD_ALLOW_SEALING)
            os.fchmod(descriptor, 0o600)
            os.write(descriptor, passphrase + b"\n")
            os.lseek(descriptor, 0, os.SEEK_SET)
            fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS,
                        fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            args = [*args, "-passin", f"fd:{descriptor}"]
        result = subprocess.run(args, cwd=cwd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=30, check=False, shell=False, umask=0o077,
                                env={"PATH": "/usr/bin:/usr/sbin", "LANG": "C", "TZ": "UTC"},
                                pass_fds=() if descriptor is None else (descriptor,))
        if result.returncode != 0:
            raise PkiError("operation_failed")
        return result.stdout
    except (OSError, subprocess.TimeoutExpired):
        raise PkiError("operation_failed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
