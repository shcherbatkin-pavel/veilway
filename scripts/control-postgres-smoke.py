#!/usr/bin/env python3

"""Exercise migrations and bootstrap against a disposable local PostgreSQL."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPOSITORY_ROOT / "web/compose.yaml"
POSTGRES_IMAGE = "postgres:17.4-alpine"


def run(command: list[str], *, environment: dict[str, str], stdin: object = None) -> None:
    subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        input=json.dumps(stdin) if stdin is not None else None,
        text=stdin is not None,
        check=True,
    )


def main() -> None:
    if shutil.which("docker") is None:
        raise SystemExit("docker is required")
    project = f"veilway-control-pg-smoke-{uuid.uuid4().hex[:8]}"
    compose = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--file",
        str(COMPOSE_FILE),
    ]
    with tempfile.TemporaryDirectory(prefix="veilway-pg-smoke-") as directory:
        root = Path(directory)
        secret_root = root / "secrets"
        data_root = root / "data"
        runtime_root = root / "run"
        secret_root.mkdir(mode=0o700)
        data_root.mkdir(mode=0o777)
        runtime_root.mkdir(mode=0o777)
        test_secrets = {
            "postgres_password": "test-postgres-password-value",
            "aws_access_key_id": "test-access-key-id-value",
            "aws_secret_access_key": "test-secret-access-key-value",
        }
        for name, value in test_secrets.items():
            path = secret_root / name
            path.write_text(value + "\n", encoding="utf-8")
            # The unprivileged host user cannot create root-owned fixtures.
            # Production Ansible instead uses root:root 0400.
            path.chmod(0o444)

        environment = os.environ.copy()
        environment.update(
            {
                "VEILWAY_SECRET_ROOT": str(secret_root),
                "VEILWAY_DATA_ROOT": str(data_root),
                "VEILWAY_RUNTIME_ROOT": str(runtime_root),
            }
        )
        try:
            run(compose + ["build", "api"], environment=environment)
            run(
                compose + ["up", "--detach", "--wait", "db"],
                environment=environment,
            )
            run(
                compose
                + ["run", "--rm", "--no-deps", "api", "alembic", "upgrade", "head"],
                environment=environment,
            )
            run(
                compose
                + [
                    "run",
                    "--rm",
                    "--no-deps",
                    "api",
                    "veilway-control",
                    "bootstrap-admin",
                ],
                environment=environment,
                stdin={
                    "login": "operator",
                    "password": "test-only-admin-password",
                },
            )
            run(
                compose
                + [
                    "run",
                    "--rm",
                    "--no-deps",
                    "api",
                    "veilway-control",
                    "sync-vms",
                ],
                environment=environment,
                stdin={
                    "aws-direct": {
                        "instance_id": "i-testonly0000000000",
                        "region": "eu-central-1",
                        "heartbeat_token": "a" * 40,
                    },
                    "yc-direct": {
                        "instance_id": "fhmtestonly0000000000",
                        "region": "ru-central1-a",
                        "heartbeat_token": "y" * 40,
                    },
                },
            )
            result = subprocess.run(
                compose
                + [
                    "exec",
                    "-T",
                    "db",
                    "psql",
                    "-U",
                    "veilway_control",
                    "-d",
                    "veilway_control",
                    "-tAc",
                    "select count(*) from vpn_vms",
                ],
                cwd=REPOSITORY_ROOT,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            if result.stdout.strip() != "2":
                raise RuntimeError("temporary database did not contain exactly two VMs")
            print("temporary PostgreSQL migration/bootstrap/sync passed")
        finally:
            subprocess.run(
                compose + ["down", "--volumes", "--remove-orphans"],
                cwd=REPOSITORY_ROOT,
                env=environment,
                check=False,
            )
            postgres_data = data_root / "postgres"
            if postgres_data.exists():
                subprocess.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--volume",
                        f"{root}:/cleanup",
                        POSTGRES_IMAGE,
                        "rm",
                        "-rf",
                        "--",
                        "/cleanup/data/postgres",
                    ],
                    cwd=REPOSITORY_ROOT,
                    check=True,
                )


if __name__ == "__main__":
    main()
