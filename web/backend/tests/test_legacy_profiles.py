"""Original-file migration through PostgreSQL, socket, roles and revocation."""
import os
from pathlib import Path
import sys
import threading
import uuid

import pytest
from sqlalchemy import func, select

from test_api import build_client, login
from test_profile_integration import db_factory
from test_migrations import postgres_connection, config
from test_profiles import user_client
from privacy_helpers import assert_database_excludes
from veilway_control.config import Settings
from veilway_control.legacy_profiles import synchronize_legacy_profiles
from veilway_control.models import ProfileAuditEvent, ProfileJob, User, VpnProfile
from veilway_control.pki import PkiClient, PkiUnavailable
from veilway_control.profile_api import get_pki_client
from veilway_control.profile_worker import ProfileWorker


@pytest.fixture
def legacy_pki(tmp_path):
    pytest.importorskip("veilway_pki")
    sys.path.insert(0, "/app/pki-fixtures")
    from test_legacy import legacy_fixture
    from test_pki import PkiTests
    from veilway_pki.legacy import import_profiles
    from veilway_pki.server import Server
    PkiTests.setUpClass()
    try:
        _, store, bundle, originals = legacy_fixture(tmp_path)
        ids = import_profiles(store, bundle)
        server = Server(tmp_path / "legacy.sock", store, allowed_uid=os.geteuid(), socket_gid=os.getegid())
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            yield PkiClient(tmp_path / "legacy.sock"), store, originals, ids
        finally:
            server.shutdown()
            thread.join(timeout=10)
            server.server_close()
    finally:
        PkiTests.tearDownClass()
        sys.path.remove("/app/pki-fixtures")


def test_legacy_import_recovery_owner_access_original_download_and_revoke(db_factory, seed_control_data, legacy_pki, caplog):
    admin_id = seed_control_data()["user_id"]
    pki, store, originals, ids = legacy_pki
    before_serial = (store.current() / "ca/serial").read_bytes()
    before_crl = (store.current() / "ca/crlnumber").read_bytes()
    with db_factory() as db:
        users = [User(role="USER", google_sub=f"legacy-user-{i}", email=f"legacy-{i}@example.test") for i in range(2)]
        db.add_all(users)
        db.commit()
        owner, other = [user.id for user in users]
    def crash(point):
        if point == "before_db_commit":
            raise RuntimeError("synthetic crash")
    with pytest.raises(RuntimeError):
        synchronize_legacy_profiles(db_factory, pki, admin_id, fault=crash)
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(VpnProfile)) == 0
    assert synchronize_legacy_profiles(db_factory, pki, admin_id) == 3
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(ProfileJob)) == 0
        assert db.scalar(select(func.count()).select_from(ProfileAuditEvent)) == 3
    with build_client(db_factory) as admin:
        admin.app.dependency_overrides[get_pki_client] = lambda: pki
        headers = {"X-CSRF-Token": login(admin)}
        worker = ProfileWorker(Settings(), db_factory, pki)
        for pid in ids:
            mode = admin.get(f"/api/v1/profiles/{pid}").json()["mode"]
            assigned = admin.post(f"/api/v1/profiles/{pid}/owner", json={"owner_id": str(owner)}, headers=headers)
            assert assigned.status_code == 200
            assert admin.patch(f"/api/v1/profiles/{pid}", json={"device_name": "renamed"}, headers=headers).status_code == 200
            for user_id, expected in ((owner, 200), (other, 404)):
                with user_client(db_factory, user_id, pki)[0] as user:
                    csrf = user.get("/api/v1/auth/session").json()["csrf_token"]
                    downloaded = user.post(f"/api/v1/profiles/{pid}/download", headers={"X-CSRF-Token": csrf})
                    assert downloaded.status_code == expected
                    if expected == 200:
                        assert downloaded.content == originals[mode]
                        assert downloaded.headers["cache-control"] == "no-store"
            assert synchronize_legacy_profiles(db_factory, pki, admin_id) == 3
            assert admin.get(f"/api/v1/profiles/{pid}").json()["owner_id"] == str(owner)
            assert admin.get(f"/api/v1/profiles/{pid}").json()["device_name"] == "renamed"
        assert (store.current() / "ca/serial").read_bytes() == before_serial
        assert (store.current() / "ca/crlnumber").read_bytes() == before_crl
        pid = ids[0]
        assert admin.post(f"/api/v1/profiles/{pid}/revoke", json={"idempotency_key": str(uuid.uuid4())}, headers=headers).status_code == 202
        assert worker.step()
        assert admin.get(f"/api/v1/profiles/{pid}").json()["status"] == "revoking"
        assert admin.post(f"/api/v1/profiles/{pid}/download", headers=headers).status_code == 409
        assert synchronize_legacy_profiles(db_factory, pki, admin_id) == 3
        assert admin.get(f"/api/v1/profiles/{pid}").json()["status"] == "revoking"
    assert_database_excludes(db_factory, [value.decode() for value in originals.values()] + ["-----BEGIN PRIVATE KEY-----", "BEGIN OpenVPN tls-crypt-v2 client key"])
    assert "BEGIN PRIVATE KEY" not in caplog.text
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(ProfileAuditEvent).where(ProfileAuditEvent.action == "import")) == 3


def test_sync_rejects_user_legacy_admin_and_conflicting_existing_profile(db_factory, seed_control_data, legacy_pki):
    admin_id = seed_control_data()["user_id"]
    pki, _, _, _ = legacy_pki
    with pytest.raises(PkiUnavailable):
        synchronize_legacy_profiles(db_factory, pki, uuid.uuid4())
    with db_factory() as db:
        user = User(role="USER", google_sub="migration-user", email="user@example.test")
        legacy_admin = User(role="ADMIN", login="legacy-operator", password_hash="synthetic-unused-hash", is_active=True)
        db.add_all([user, legacy_admin])
        db.commit()
        rejected = [user.id, legacy_admin.id]
    for actor_id in rejected:
        with pytest.raises(PkiUnavailable):
            synchronize_legacy_profiles(db_factory, pki, actor_id)
    assert synchronize_legacy_profiles(db_factory, pki, admin_id) == 3
    with db_factory() as db:
        db.scalar(select(VpnProfile)).legacy_import_sha256 = "0" * 64
        db.commit()
    with pytest.raises(PkiUnavailable):
        synchronize_legacy_profiles(db_factory, pki, admin_id)


def test_operator_cli_synchronizes_through_the_real_socket(db_factory, seed_control_data, legacy_pki, monkeypatch, capsys):
    from veilway_control import cli, config as settings_module
    admin_id = seed_control_data()["user_id"]
    pki = legacy_pki[0]
    monkeypatch.setattr(cli, "get_session_factory", lambda: db_factory)
    monkeypatch.setattr(settings_module, "get_settings", lambda: Settings(pki_socket_path=pki.socket_path))
    monkeypatch.setattr(sys, "argv", ["veilway-control", "import-legacy-profiles", "--admin-id", str(admin_id)])
    cli.main()
    assert "synchronized: 3" in capsys.readouterr().out
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(VpnProfile)) == 3


def test_migration_preserves_provenance_and_refuses_downgrade(postgres_connection):
    from alembic import command
    import sqlalchemy as sa
    cfg = config(postgres_connection)
    command.upgrade(cfg, "head")
    postgres_connection.execute(sa.text("""
        INSERT INTO users(id,google_sub,email,role,is_active,created_at,updated_at)
        VALUES ('10000000-0000-4000-8000-000000000001','legacy-synthetic-admin','operator@example.test','ADMIN',true,now(),now())
    """))
    postgres_connection.execute(sa.text("""
        INSERT INTO vpn_profiles(id,device_name,mode,created_by_id,status,created_at,expires_at,legacy_import_sha256)
        VALUES ('20000000-0000-4000-8000-000000000001','legacy','yc-direct','10000000-0000-4000-8000-000000000001','active',now(),now()+interval '1 day',repeat('a',64))
    """))
    postgres_connection.commit()
    with pytest.raises(RuntimeError, match="operator-preserving migration"):
        command.downgrade(cfg, "0005_crl_delivery")
    postgres_connection.rollback()
    assert postgres_connection.scalar(sa.text("SELECT legacy_import_sha256 FROM vpn_profiles")) == "a" * 64
    assert postgres_connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0006_legacy_profiles"
