#!/usr/bin/env python3

"""Load root-only Docker secrets, then permanently drop privileges."""

from __future__ import annotations

import fcntl
import os
import sys
from pathlib import Path


RUNTIME_UID = 10001
RUNTIME_GID = 10001
SECRET_PATHS = {
    "VEILWAY_DATABASE_PASSWORD_FILE": Path("/run/secrets/postgres_password"),
    "VEILWAY_AWS_ACCESS_KEY_ID_FILE": Path("/run/secrets/aws_access_key_id"),
    "VEILWAY_AWS_SECRET_ACCESS_KEY_FILE": Path(
        "/run/secrets/aws_secret_access_key"
    ),
}


def protected_secret_fd(label: str, source: Path) -> int:
    value = source.read_bytes()
    if not value.strip() or len(value) > 65536:
        raise RuntimeError(f"invalid runtime secret: {label}")
    descriptor = os.memfd_create(label, flags=os.MFD_ALLOW_SEALING)
    os.write(descriptor, value)
    os.lseek(descriptor, 0, os.SEEK_SET)
    fcntl.fcntl(
        descriptor,
        fcntl.F_ADD_SEALS,
        fcntl.F_SEAL_SEAL
        | fcntl.F_SEAL_SHRINK
        | fcntl.F_SEAL_GROW
        | fcntl.F_SEAL_WRITE,
    )
    os.set_inheritable(descriptor, True)
    return descriptor


def command_for(arguments: list[str]) -> tuple[list[str], Path | None]:
    if not arguments or arguments == ["serve"]:
        socket_path = Path("/run/veilway/api.sock")
        return (
            [
                "uvicorn",
                "veilway_control.main:app",
                "--uds",
                str(socket_path),
                "--proxy-headers",
                "--forwarded-allow-ips=*",
                "--no-access-log",
            ],
            socket_path,
        )
    return arguments, None


def main() -> int:
    if os.geteuid() != 0:
        raise RuntimeError("container entrypoint must begin as root")
    environment = os.environ.copy()
    for variable, source in SECRET_PATHS.items():
        descriptor = protected_secret_fd(variable.lower(), source)
        environment[variable] = f"/proc/self/fd/{descriptor}"

    command, socket_path = command_for(sys.argv[1:])
    os.setgroups([])
    os.setgid(RUNTIME_GID)
    os.setuid(RUNTIME_UID)
    os.umask(0o077)
    if socket_path is not None:
        socket_path.unlink(missing_ok=True)
    os.execvpe(command[0], command, environment)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
