"""Real PostgreSQL migration tests; only an explicit disposable test URL is used."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
import uuid
from datetime import timedelta

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from test_api import build_client, login
from veilway_control.database import Base
from veilway_control.models import GoogleAdminBinding, ProfileJob, RestartJob, User, VpnProfile, utcnow
from veilway_control.security import PASSWORD_HASHER, hash_token, verify_password
from veilway_control.oidc import GoogleIdentity, resolve_google_user


@pytest.fixture
def postgres_connection():
    url = os.environ.get("VEILWAY_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("requires explicit VEILWAY_TEST_POSTGRES_URL for disposable PostgreSQL")
    engine = sa.create_engine(url)
    schema = "veilway_test_" + uuid.uuid4().hex
    with engine.connect() as connection:
        connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(sa.text(f'SET search_path TO "{schema}"'))
        connection.commit()
        try:
            yield connection
        finally:
            connection.rollback()
            connection.execute(sa.text("SET search_path TO public"))
            connection.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
            connection.commit()
    engine.dispose()


def config(connection):
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    cfg.attributes["connection"] = connection
    return cfg


def dump(connection, name):
    table = sa.Table(name, sa.MetaData(), autoload_with=connection)
    return [dict(row) for row in connection.execute(sa.select(table)).mappings()]


def seed_legacy(connection):
    metadata = sa.MetaData()
    metadata.reflect(bind=connection)
    now = utcnow()
    admin_id, session_id, vm_id, job_id, target_id, heartbeat_id = [uuid.uuid4() for _ in range(6)]
    connection.execute(metadata.tables["admins"].insert(), {
        "id": admin_id, "login": "operator",
        "password_hash": PASSWORD_HASHER.hash("correct horse battery staple"),
        "created_at": now, "updated_at": now,
    })
    connection.execute(metadata.tables["admin_sessions"].insert(), {
        "id": session_id, "admin_id": admin_id, "token_hash": hash_token("test-migration-session"),
        "csrf_hash": hash_token("test-migration-csrf"), "created_at": now,
        "expires_at": now + timedelta(hours=8), "last_seen_at": now,
    })
    connection.execute(metadata.tables["vpn_vms"].insert(), {
        "id": vm_id, "slug": "aws-direct", "provider": "aws", "instance_id": "i-testonly0000000000",
        "region": "eu-central-1", "restart_order": 1, "heartbeat_token_hash": hash_token("test-heartbeat"),
        "created_at": now,
    })
    connection.execute(metadata.tables["vm_heartbeats"].insert(), {
        "id": heartbeat_id, "vm_id": vm_id, "boot_id": str(uuid.uuid4()), "healthy": True,
        "uptime_seconds": 100, "received_at": now,
    })
    connection.execute(metadata.tables["restart_jobs"].insert(), {
        "id": job_id, "admin_id": admin_id, "status": "succeeded", "active_guard": None,
        "created_at": now, "started_at": now, "finished_at": now, "error_code": None,
    })
    connection.execute(metadata.tables["restart_targets"].insert(), {
        "id": target_id, "job_id": job_id, "vm_id": vm_id, "position": 1, "status": "recovered",
        "previous_boot_id": str(uuid.uuid4()), "cloud_request_id": "test-request",
        "dispatched_at": now, "recovered_at": now, "error_code": None,
    })
    connection.commit()
    return {name: dump(connection, name) for name in (
        "admins", "admin_sessions", "vpn_vms", "vm_heartbeats", "restart_jobs", "restart_targets",
    )}


def upgrade_legacy(connection):
    cfg = config(connection)
    command.upgrade(cfg, "0001_restart_control_plane")
    before = seed_legacy(connection)
    connection.commit()
    command.upgrade(cfg, "0002_users_and_profiles")
    return cfg, before


def test_stage1_upgrade_preserves_all_legacy_data_and_password_material(postgres_connection):
    connection = postgres_connection
    _, before = upgrade_legacy(connection)
    migrated = dump(connection, "users")
    legacy_columns = before["admins"][0].keys()
    assert [{key: row[key] for key in legacy_columns} for row in migrated] == before["admins"]
    assert migrated[0]["role"] == "ADMIN" and migrated[0]["is_active"]
    assert migrated[0]["google_sub"] is None and migrated[0]["email"] is None
    for old, new in (("admin_sessions", "user_sessions"), ("restart_jobs", "restart_jobs")):
        expected = [dict(row, user_id=row["admin_id"]) for row in before[old]]
        for row in expected:
            del row["admin_id"]
        assert dump(connection, new) == expected
    for name in ("vpn_vms", "vm_heartbeats", "restart_targets"):
        assert dump(connection, name) == before[name]
    connection.commit()

    factory = sessionmaker(bind=connection, expire_on_commit=False)
    assert verify_password(migrated[0]["password_hash"], "correct horse battery staple")
    with factory() as db:
        legacy_id = before["admins"][0]["id"]
        user = User(google_sub="test-new-subject", email="operator@example.invalid")
        db.add(user)
        db.commit()
        assert user.role == "USER" and user.id != legacy_id
        assert db.get(User, legacy_id).google_sub is None
        assert db.get(RestartJob, before["restart_jobs"][0]["id"]).user_id == legacy_id


def test_legacy_only_downgrade_preserves_all_rows(postgres_connection):
    connection = postgres_connection
    cfg, before = upgrade_legacy(connection)
    command.downgrade(cfg, "0001_restart_control_plane")
    for name, rows in before.items():
        assert dump(connection, name) == rows
    connection.commit()
    command.upgrade(cfg, "0002_users_and_profiles")
    assert dump(connection, "users")[0]["id"] == before["admins"][0]["id"]


@pytest.mark.parametrize("incompatible", ["google_user", "profile", "disabled_legacy"])
def test_downgrade_refuses_data_loss_or_credential_reactivation(postgres_connection, incompatible):
    connection = postgres_connection
    cfg, before = upgrade_legacy(connection)
    with Session(bind=connection) as db:
        legacy = db.get(User, before["admins"][0]["id"])
        if incompatible == "google_user":
            db.add(User(google_sub="test-google-user", email="test@example.invalid"))
        elif incompatible == "disabled_legacy":
            legacy.is_active = False
        else:
            # Seed the historical stage-1 schema, independently of later ORM fields.
            profile_id = uuid.uuid4()
            now = utcnow()
            profile_table = sa.Table("vpn_profiles", sa.MetaData(), autoload_with=connection)
            job_table = sa.Table("profile_jobs", sa.MetaData(), autoload_with=connection)
            db.execute(profile_table.insert(), {"id": profile_id, "device_name": "Test device", "mode": "yc-direct",
                "created_by_id": legacy.id, "status": "issuing", "created_at": now, "expires_at": now + timedelta(days=365)})
            db.execute(job_table.insert(), {"id": uuid.uuid4(), "profile_id": profile_id, "requested_by_id": legacy.id,
                "kind": "issue", "status": "queued", "idempotency_key": uuid.uuid4(), "created_at": now})
        db.commit()
    snapshots = {name: dump(connection, name) for name in (
        "users", "user_sessions", "restart_jobs", "restart_targets", "vpn_profiles", "profile_jobs",
    )}
    connection.commit()
    with pytest.raises(RuntimeError, match="rollback requires an operator migration"):
        command.downgrade(cfg, "0001_restart_control_plane")
    connection.rollback()
    for name, rows in snapshots.items():
        assert dump(connection, name) == rows
    assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0002_users_and_profiles"


def test_google_switch_revokes_sessions_preserves_history_and_matches_models(postgres_connection):
    connection = postgres_connection
    cfg, before = upgrade_legacy(connection)
    command.upgrade(cfg, "head")
    assert dump(connection, "user_sessions") == []
    legacy = dump(connection, "users")[0]
    assert not legacy["is_active"] and legacy["google_sub"] is None
    assert legacy["id"] == before["admins"][0]["id"]
    assert legacy["password_hash"] == before["admins"][0]["password_hash"]
    assert dump(connection, "restart_jobs")[0]["user_id"] == legacy["id"]
    assert dump(connection, "restart_targets") == before["restart_targets"]
    assert dump(connection, "google_admin_binding") == [{"id": 1, "user_id": None}]
    assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    connection.commit()
    factory = sessionmaker(bind=connection, expire_on_commit=False)
    with build_client(factory) as client:
        client.cookies.set("__Host-veilway_session", "test-migration-session")
        assert client.get("/api/v1/auth/session").status_code == 401
        assert client.post("/api/v1/auth/login", json={"login": "operator", "password": "wrong"}).status_code == 404
        login(client)
        session = client.get("/api/v1/auth/session").json()
        assert session["role"] == "ADMIN" and session["user_id"] != str(legacy["id"])
        assert client.get("/api/v1/restart-jobs").json()[0]["id"] == str(before["restart_jobs"][0]["id"])
    with factory() as db:
        binding = db.get(GoogleAdminBinding, 1)
        assert str(binding.user_id) == session["user_id"]
        assert db.get(User, legacy["id"]).google_sub is None
    connection.commit()
    with pytest.raises(RuntimeError, match="administrator binding"):
        command.downgrade(cfg, "0002_users_and_profiles")
    connection.rollback()
    assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0007_heartbeat_details"


def test_unbound_google_downgrade_does_not_reactivate_legacy_users(postgres_connection):
    connection = postgres_connection
    cfg, _ = upgrade_legacy(connection)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0002_users_and_profiles")
    assert dump(connection, "user_sessions") == []
    assert not dump(connection, "users")[0]["is_active"]


def test_google_switch_also_revokes_existing_google_sessions_and_roles(postgres_connection):
    connection = postgres_connection
    cfg, _ = upgrade_legacy(connection)
    with Session(bind=connection) as db:
        user = User(google_sub="pre-switch-google", email="test@gmail.com", role="ADMIN")
        db.add(user)
        db.flush()
        from veilway_control.security import create_user_session
        from veilway_control.config import Settings
        create_user_session(db, user, Settings(public_host="testserver"))
        user_id = user.id
    command.upgrade(cfg, "head")
    assert dump(connection, "user_sessions") == []
    with Session(bind=connection) as db:
        user = db.get(User, user_id)
        assert user.role == "USER" and user.is_active and user.google_sub == "pre-switch-google"


def test_concurrent_admin_bootstrap_binds_exactly_one_google_identity(postgres_connection):
    connection = postgres_connection
    cfg, _ = upgrade_legacy(connection)
    command.upgrade(cfg, "head")
    schema = connection.scalar(sa.text("SELECT current_schema()"))
    connection.commit()
    barrier = Barrier(2)

    def register(subject):
        with connection.engine.connect() as worker_connection:
            # The schema name was generated by the fixture, never supplied by a request.
            worker_connection.execute(sa.text(f'SET search_path TO "{schema}"'))
            worker_connection.commit()
            with Session(bind=worker_connection, expire_on_commit=False) as db:
                barrier.wait(timeout=10)
                user = resolve_google_user(db, GoogleIdentity(subject, "operator@gmail.com", True), "operator@gmail.com")
                db.commit()
                return user.id, user.role

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(register, ["concurrent-sub-1", "concurrent-sub-2"]))
    assert sorted(role for _, role in results) == ["ADMIN", "USER"]
    admin_id = next(user_id for user_id, role in results if role == "ADMIN")
    assert dump(connection, "google_admin_binding") == [{"id": 1, "user_id": admin_id}]


def test_observability_upgrade_preserves_existing_heartbeat(postgres_connection):
    connection = postgres_connection
    cfg, _ = upgrade_legacy(connection)
    command.upgrade(cfg, "0006_legacy_profiles")
    before = dump(connection, "vm_heartbeats")
    command.upgrade(cfg, "head")
    after = dump(connection, "vm_heartbeats")
    assert all(row["containers"] is None for row in after)
    assert [{key: row[key] for key in before[0]} for row in after] == before
    connection.execute(sa.text("UPDATE vm_heartbeats SET containers = :value"),
                       {"value": '{"veilway-openvpn":"healthy"}'})
    connection.commit()
    assert dump(connection, "vm_heartbeats")[0]["containers"] == {"veilway-openvpn": "healthy"}
    command.downgrade(cfg, "0006_legacy_profiles")
    assert dump(connection, "vm_heartbeats") == before
    command.upgrade(cfg, "head")
    assert dump(connection, "vm_heartbeats")[0]["containers"] is None
