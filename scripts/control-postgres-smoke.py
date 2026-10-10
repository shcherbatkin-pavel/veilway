#!/usr/bin/env python3

"""Exercise migrations and VM sync against a disposable local PostgreSQL."""

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
        pki_runtime_root = root / "pki-run"
        secret_root.mkdir(mode=0o700)
        data_root.mkdir(mode=0o777)
        runtime_root.mkdir(mode=0o777)
        pki_runtime_root.mkdir(mode=0o755)
        test_secrets = {
            "postgres_password": "test-postgres-password-value",
            "aws_access_key_id": "test-access-key-id-value",
            "aws_secret_access_key": "test-secret-access-key-value",
            "google_client_id": "test-google-client-id",
            "google_client_secret": "test-only-google-client-secret",
            "admin_google_email": "test-admin@gmail.com",
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
                "VEILWAY_PKI_RUNTIME_ROOT": str(pki_runtime_root),
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
            # Synthetic recovery metadata; no real CA or reusable token.
            seed = """
INSERT INTO users (id,google_sub,email,role,is_active,created_at,updated_at)
VALUES ('10000000-0000-4000-8000-000000000001','restore-synthetic-admin','operator@example.test','ADMIN',true,now(),now());
UPDATE google_admin_binding SET user_id='10000000-0000-4000-8000-000000000001' WHERE id=1;
INSERT INTO users (id,google_sub,email,role,is_active,created_at,updated_at)
VALUES ('10000000-0000-4000-8000-000000000002','restore-synthetic-owner','owner@example.test','USER',true,now(),now());
INSERT INTO vpn_profiles (id,device_name,mode,created_by_id,owner_id,status,created_at,expires_at,certificate_serial,certificate_sha256,legacy_import_sha256)
VALUES ('20000000-0000-4000-8000-000000000001','Synthetic restored profile','yc-direct','10000000-0000-4000-8000-000000000001','10000000-0000-4000-8000-000000000002','revoked',now(),now()+interval '3 days','1234',repeat('a',64),repeat('c',64));
INSERT INTO profile_audit_events (id,actor_id,action,object_id,result,created_at)
VALUES ('30000000-0000-4000-8000-000000000001','10000000-0000-4000-8000-000000000001','import','20000000-0000-4000-8000-000000000001','succeeded',now());
INSERT INTO crl_agents (slug,token_hash,acknowledged_version,acknowledged_sha256,acknowledged_until)
VALUES ('yc-direct',decode(repeat('ab',32),'hex'),4099,repeat('b',64),now()+interval '7 days');
"""
            subprocess.run(compose + ["exec", "-T", "db", "psql", "-U", "veilway_control", "-d", "veilway_control", "-v", "ON_ERROR_STOP=1"],
                           cwd=REPOSITORY_ROOT, env=environment, input=seed, text=True,
                           stdout=subprocess.PIPE, check=True)
            archive = subprocess.run(compose + ["exec", "-T", "db", "pg_dump", "-U", "veilway_control", "-d", "veilway_control",
                                                "--format=custom", "--no-owner", "--no-acl"],
                                     cwd=REPOSITORY_ROOT, env=environment, stdout=subprocess.PIPE, check=True).stdout
            subprocess.run(compose + ["exec", "-T", "db", "createdb", "-U", "veilway_control", "veilway_restore_test"],
                           cwd=REPOSITORY_ROOT, env=environment, check=True)
            subprocess.run(compose + ["exec", "-T", "db", "pg_restore", "-U", "veilway_control", "--dbname", "veilway_restore_test",
                                      "--single-transaction", "--exit-on-error", "--no-owner", "--no-acl"],
                           cwd=REPOSITORY_ROOT, env=environment, input=archive, check=True)
            restored = subprocess.run(compose + ["exec", "-T", "db", "psql", "-U", "veilway_control", "-d", "veilway_restore_test", "-tAc",
                "SELECT (SELECT count(*) FROM vpn_vms)=2 AND "
                "(SELECT status FROM vpn_profiles WHERE id='20000000-0000-4000-8000-000000000001')='revoked' AND "
                "(SELECT legacy_import_sha256 FROM vpn_profiles WHERE id='20000000-0000-4000-8000-000000000001')=repeat('c',64) AND "
                "(SELECT owner_id FROM vpn_profiles WHERE id='20000000-0000-4000-8000-000000000001')='10000000-0000-4000-8000-000000000002' AND "
                "(SELECT action FROM profile_audit_events WHERE id='30000000-0000-4000-8000-000000000001')='import' AND "
                "(SELECT user_id FROM google_admin_binding WHERE id=1)='10000000-0000-4000-8000-000000000001' AND "
                "(SELECT acknowledged_version FROM crl_agents WHERE slug='yc-direct')=4099 AND "
                "(SELECT acknowledged_sha256 FROM crl_agents WHERE slug='yc-direct')=repeat('b',64) AND "
                "(SELECT version_num FROM alembic_version)='0007_heartbeat_details'"],
                cwd=REPOSITORY_ROOT, env=environment, capture_output=True, text=True, check=True)
            if restored.stdout.strip() != "t":
                raise RuntimeError("synthetic PostgreSQL recovery metadata did not match")
            print("temporary PostgreSQL dump/restore preserved binding, revoked profile and CRL acknowledgement")
            print("temporary PostgreSQL migration/sync passed")
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
