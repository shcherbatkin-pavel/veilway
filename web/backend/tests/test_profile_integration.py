"""HTTP -> PostgreSQL (when supplied) -> socket -> real synthetic CA."""
from datetime import timedelta
import os
from pathlib import Path
import sys
import threading
import uuid

import pytest
from sqlalchemy import select
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from alembic import command

from test_api import build_client, login
from test_profiles import payload, user_client
from test_migrations import postgres_connection, config
from privacy_helpers import assert_database_excludes
from veilway_control.config import Settings
from veilway_control.models import ProfileAuditEvent, ProfileJob, User, VpnProfile, utcnow
from veilway_control.pki import PkiClient
from veilway_control.profile_api import get_pki_client
from veilway_control.profile_worker import ProfileWorker


@pytest.fixture
def db_factory(db_factory, request):
    if not os.environ.get("VEILWAY_TEST_POSTGRES_URL"):
        yield db_factory
        return
    connection = request.getfixturevalue("postgres_connection")
    command.upgrade(config(connection), "head")
    connection.commit()
    schema = connection.scalar(sa.text("SELECT current_schema()"))
    engine = sa.create_engine(connection.engine.url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()


@pytest.fixture
def real_pki(tmp_path):
    pytest.importorskip("veilway_pki", reason="requires isolated profile integration image")
    sys.path.insert(0, "/app/pki-fixtures")
    from test_pki import PkiTests
    from veilway_pki.core import Store
    from veilway_pki.server import Server
    PkiTests.setUpClass()
    try:
        store = Store(tmp_path / "store", PkiTests.password)
        store.import_ca(PkiTests.source, PkiTests.endpoints)
        server = Server(tmp_path / "pki.sock", store, allowed_uid=os.geteuid(), socket_gid=os.getegid())
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            yield PkiClient(tmp_path / "pki.sock"), store
        finally:
            server.shutdown()
            thread.join(timeout=10)
            server.server_close()
    finally:
        PkiTests.tearDownClass()
        sys.path.remove("/app/pki-fixtures")


def test_admin_issues_each_mode_assigns_user_and_only_owner_downloads_real_ovpn(db_factory, seed_control_data, real_pki, caplog):
    pki, store = real_pki
    seed_control_data()
    with db_factory() as db:
        owner = User(role="USER", google_sub="integration-owner", email="owner@example.test")
        other = User(role="USER", google_sub="integration-other", email="other@example.test")
        db.add_all([owner, other])
        db.commit()
        owner_id, other_id = owner.id, other.id
    with build_client(db_factory) as admin:
        headers = {"X-CSRF-Token": login(admin)}
        admin.app.dependency_overrides[get_pki_client] = lambda: pki
        worker = ProfileWorker(Settings(), db_factory, pki)
        for mode in ("yc-direct", "aws-direct", "yc-aws-multihop"):
            data = payload(mode=mode, duration_days=3)
            created = admin.post("/api/v1/profiles", json=data, headers=headers)
            assert created.status_code == 202
            pid, job_id = created.json()["profile"]["id"], created.json()["job"]["id"]
            assert worker.step()
            assert admin.get(f"/api/v1/profile-jobs/{job_id}").json()["status"] == "succeeded"
            assigned = admin.post(f"/api/v1/profiles/{pid}/owner", json={"owner_id": str(owner_id)}, headers=headers)
            assert assigned.status_code == 200
            with user_client(db_factory, owner_id, pki)[0] as user:
                csrf = user.get("/api/v1/auth/session").json()["csrf_token"]
                downloaded = user.post(f"/api/v1/profiles/{pid}/download", headers={"X-CSRF-Token": csrf})
                assert downloaded.status_code == 200
                assert b"<key>\n-----BEGIN PRIVATE KEY-----" in downloaded.content
                assert b"BEGIN OpenVPN tls-crypt-v2 client key" in downloaded.content
                assert downloaded.headers["cache-control"] == "no-store"
                assert user.get(f"/api/v1/profiles/{pid}").json()["mode"] == mode
            with user_client(db_factory, other_id, pki)[0] as user:
                csrf = user.get("/api/v1/auth/session").json()["csrf_token"]
                assert user.post(f"/api/v1/profiles/{pid}/download", headers={"X-CSRF-Token": csrf}).status_code == 404
                assert user.get(f"/api/v1/profile-jobs/{job_id}").status_code == 404
            assert admin.post("/api/v1/profiles", json=data, headers=headers).json()["profile"]["id"] == pid
            revoked = admin.post(f"/api/v1/profiles/{pid}/revoke", json={"idempotency_key": str(uuid.uuid4())}, headers=headers)
            assert revoked.status_code == 202
            assert worker.step()
            assert admin.get(f"/api/v1/profiles/{pid}").json()["status"] == "revoking"
            assert admin.post(f"/api/v1/profiles/{pid}/download", headers=headers).status_code == 409
        assert len(list((store.current() / "profiles").iterdir())) == 3
        with db_factory() as db:
            for row in db.scalars(select(VpnProfile)):
                assert row.certificate_serial and row.certificate_sha256
            for row in db.scalars(select(ProfileAuditEvent)):
                assert "PRIVATE KEY" not in str(row.__dict__)
        assert_database_excludes(db_factory, [
            "-----BEGIN PRIVATE KEY-----", "-----BEGIN CERTIFICATE-----",
            "BEGIN OpenVPN tls-crypt-v2 client key", downloaded.text,
        ])
        assert "-----BEGIN PRIVATE KEY-----" not in caplog.text
        assert "BEGIN OpenVPN tls-crypt-v2 client key" not in caplog.text


def test_ca_expiry_is_authoritative_without_issuing_an_extra_certificate(db_factory, seed_control_data, real_pki):
    pki, store = real_pki
    seed_control_data()
    before = (store.current() / "ca/serial").read_text()
    with build_client(db_factory) as admin:
        headers = {"X-CSRF-Token": login(admin)}
        created = admin.post("/api/v1/profiles", json=payload(), headers=headers)
        assert created.status_code == 202
        assert ProfileWorker(Settings(), db_factory, pki).step()
        job = admin.get(f"/api/v1/profile-jobs/{created.json()['job']['id']}").json()
        assert job["status"] == "failed" and job["error_code"] == "pki_expiry_rejected"
        assert admin.get(f"/api/v1/profiles/{created.json()['profile']['id']}").json()["status"] == "failed"
    assert (store.current() / "ca/serial").read_text() == before
