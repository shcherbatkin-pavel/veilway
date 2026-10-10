from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import select

from test_api import build_client, login
from veilway_control.config import Settings
from veilway_control.models import ProfileAuditEvent, ProfileJob, User, VpnProfile, as_utc, utcnow
from veilway_control.pki import PkiUnavailable
from veilway_control.profile_api import get_pki_client
from veilway_control.profile_worker import ProfileWorker
from veilway_control.schemas import ProfileCreateRequest
from veilway_control.profiles import create_profile
from veilway_control.security import create_user_session


class FakePki:
    def __init__(self):
        self.issued = {}
        self.revoked = {}
        self.calls = []
        self.error = None

    def issue(self, profile_id, key, mode, expires_at):
        self.calls.append(("issue", key))
        if self.error:
            raise PkiUnavailable(self.error)
        if key not in self.issued:
            self.issued[key] = dict(profile_id=str(profile_id), mode=mode, expires_at=expires_at,
                                    serial=f'{4096 + len(self.issued):X}', certificate_sha256="a" * 64)
        return self.issued[key]

    def revoke(self, profile_id, key):
        self.calls.append(("revoke", key))
        if self.error:
            raise PkiUnavailable(self.error)
        self.revoked.setdefault(key, dict(profile_id=str(profile_id), crl_number=4097 + len(self.revoked)))
        return self.revoked[key]

    def download(self, profile_id):
        if self.error:
            raise PkiUnavailable(self.error)
        return b"synthetic test-only profile"


@pytest.fixture
def scene(db_factory, seed_control_data):
    seed_control_data()
    with db_factory() as db:
        admin = db.scalar(select(User).where(User.role == "ADMIN"))
        users = [User(google_sub=f"profile-user-{i}", email=f"profile-{i}@example.test", role="USER") for i in range(3)]
        users[2].is_active = False
        db.add_all(users)
        db.commit()
        ids = [user.id for user in users]
        admin_id = admin.id
    pki = FakePki()
    with build_client(db_factory) as client:
        client.app.dependency_overrides[get_pki_client] = lambda: pki
        csrf = login(client)
        yield client, {"X-CSRF-Token": csrf}, ids, admin_id, pki


def payload(owner=None, **values):
    return {"idempotency_key": str(uuid.uuid4()), "device_name": "Laptop", "mode": "yc-direct", "owner_id": str(owner) if owner else None, **values}


def user_client(db_factory, user_id, pki):
    client = build_client(db_factory)
    client.app.dependency_overrides[get_pki_client] = lambda: pki
    with db_factory() as db:
        _, token, csrf = create_user_session(db, db.get(User, user_id), Settings())
    client.cookies.set("__Host-veilway_session", token)
    return client, {"X-CSRF-Token": csrf}


def create(client, headers, data):
    response = client.post("/api/v1/profiles", json=data, headers=headers)
    assert response.status_code == 202, response.text
    return response.json()


def test_full_admin_owner_user_matrix_and_safe_repeatable_download(scene, db_factory):
    client, headers, owners, admin, pki = scene
    data = create(client, headers, payload(owners[0]))
    profile_id, job_id = data["profile"]["id"], data["job"]["id"]
    assert data["profile"]["status"] == "issuing"
    assert client.post(f"/api/v1/profiles/{profile_id}/download", headers=headers).status_code == 409
    assert ProfileWorker(Settings(), db_factory, pki).step()
    response = client.get(f"/api/v1/profiles/{profile_id}")
    assert response.json()["status"] == "active"
    assert response.headers["cache-control"] == "no-store"
    assert client.get(f"/api/v1/profile-jobs/{job_id}").json()["status"] == "succeeded"
    for index, owner in enumerate(owners[:2]):
        with user_client(db_factory, owner, pki)[0] as user:
            csrf = user.get("/api/v1/auth/session").json()["csrf_token"]
            own_headers = {"X-CSRF-Token": csrf}
            expected = 200 if index == 0 else 404
            assert user.get(f"/api/v1/profiles/{profile_id}").status_code == expected
            assert user.get(f"/api/v1/profile-jobs/{job_id}").status_code == expected
            assert len(user.get("/api/v1/profiles").json()) == (1 if index == 0 else 0)
            assert len(user.get("/api/v1/profile-jobs").json()) == (1 if index == 0 else 0)
            assert user.get("/api/v1/users").status_code == 403
            assert user.get("/api/v1/profile-audit-events").status_code == 403
            assert user.post("/api/v1/profiles", json=payload(owner), headers=own_headers).status_code == 403
            assert user.patch(f"/api/v1/profiles/{profile_id}", json={"device_name": "x"}, headers=own_headers).status_code == 403
            assert user.post(f"/api/v1/profiles/{profile_id}/owner", json={"owner_id": str(owner)}, headers=own_headers).status_code == 403
            assert user.post(f"/api/v1/profiles/{profile_id}/revoke", json={"idempotency_key": str(uuid.uuid4())}, headers=own_headers).status_code == 403
            for _ in range(2):
                download = user.post(f"/api/v1/profiles/{profile_id}/download", headers=own_headers)
                assert download.status_code == expected
                if expected == 200:
                    assert download.content == b"synthetic test-only profile"
                    assert download.headers["cache-control"] == "no-store"
                    assert download.headers["pragma"] == "no-cache"
                    assert download.headers["referrer-policy"] == "no-referrer"
                    assert download.headers["content-disposition"] == f'attachment; filename="veilway-{profile_id}.ovpn"'
                    assert download.headers["x-content-type-options"] == "nosniff"
            assert user.get(f"/api/v1/profiles/{profile_id}/download").status_code == 405
            assert user.post(f"/api/v1/profiles/{profile_id}/download").status_code == 403
    with db_factory() as db:
        profile = db.get(VpnProfile, uuid.UUID(profile_id))
        assert profile.certificate_serial is not None
        assert len(profile.certificate_sha256) == 64
        events = db.scalars(select(ProfileAuditEvent)).all()
        assert {event.actor_id for event in events} >= {admin, owners[0]}
        assert all(not hasattr(event, "material") for event in events)
        assert all("synthetic" not in str(event.__dict__) for event in events)


def test_unassigned_assign_once_rename_and_same_device_can_have_multiple_profiles(scene, db_factory):
    client, headers, owners, _, pki = scene
    first = create(client, headers, payload())
    second = create(client, headers, payload())
    assert first["profile"]["id"] != second["profile"]["id"]
    pid = first["profile"]["id"]
    assert first["profile"]["owner_id"] is None
    with user_client(db_factory, owners[0], pki)[0] as user:
        assert user.get(f"/api/v1/profiles/{pid}").status_code == 404
    renamed = client.patch(f"/api/v1/profiles/{pid}", json={"device_name": "  Phone 📱  "}, headers=headers)
    assert renamed.status_code == 200
    assert renamed.json()["device_name"] == "Phone 📱"
    assert renamed.json()["id"] == pid
    for _ in range(2):
        assert client.post(f"/api/v1/profiles/{pid}/owner", json={"owner_id": str(owners[0])}, headers=headers).status_code == 200
    assert client.post(f"/api/v1/profiles/{pid}/owner", json={"owner_id": str(owners[1])}, headers=headers).status_code == 409
    assert client.post(f"/api/v1/profiles/{pid}/owner", json={"owner_id": None}, headers=headers).status_code == 422
    assert client.get(f"/api/v1/profiles/{pid}").json()["owner_id"] == str(owners[0])
    assert {user["id"] for user in client.get("/api/v1/users").json()} == {str(owner) for owner in owners[:2]}


def test_revoke_blocks_download_immediately_and_waits_for_stage5_ack(scene, db_factory):
    client, headers, owners, _, pki = scene
    created = create(client, headers, payload(owners[0]))
    ProfileWorker(Settings(), db_factory, pki).step()
    pid = created["profile"]["id"]
    key = {"idempotency_key": str(uuid.uuid4())}
    revoke = client.post(f"/api/v1/profiles/{pid}/revoke", json=key, headers=headers)
    assert revoke.status_code == 202
    assert client.post(f"/api/v1/profiles/{pid}/download", headers=headers).status_code == 409
    assert ProfileWorker(Settings(), db_factory, pki).step()
    assert client.get(f"/api/v1/profiles/{pid}").json()["status"] == "revoking"
    assert client.get(f"/api/v1/profile-jobs/{revoke.json()['id']}").json()["status"] == "succeeded"
    for value in (key, {"idempotency_key": str(uuid.uuid4())}):
        repeated = client.post(f"/api/v1/profiles/{pid}/revoke", json=value, headers=headers)
        assert repeated.status_code == 202 and repeated.json()["id"] == revoke.json()["id"]
    assert len(pki.revoked) == 1
    assert client.post(f"/api/v1/profiles/{pid}/owner", json={"owner_id": str(owners[1])}, headers=headers).status_code == 409


@pytest.mark.parametrize("status,offset,accepted", [
    ("expired", 1, False),
    ("expired", -1, False),
    ("active", -1, False),
    ("active", 0, False),
    ("active", 1, True),
])
def test_new_revoke_checks_status_and_exact_expiry(scene, db_factory, monkeypatch, status, offset, accepted):
    client, headers, _, _, pki = scene
    created = create(client, headers, payload())
    worker = ProfileWorker(Settings(), db_factory, pki)
    assert worker.step()
    pid = uuid.UUID(created["profile"]["id"])
    now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    monkeypatch.setattr("veilway_control.profiles.utcnow", lambda: now)
    with db_factory() as db:
        row = db.get(VpnProfile, pid)
        row.created_at = now - timedelta(days=1)
        row.expires_at = now + timedelta(seconds=offset)
        row.status = status
        db.commit()
    calls = list(pki.calls)
    response = client.post(f"/api/v1/profiles/{pid}/revoke",
                           json={"idempotency_key": str(uuid.uuid4())}, headers=headers)
    assert response.status_code == (202 if accepted else 409)
    with db_factory() as db:
        assert db.get(VpnProfile, pid).status == ("revoking" if accepted else status)
        jobs = db.scalars(select(ProfileJob).where(ProfileJob.profile_id == pid, ProfileJob.kind == "revoke")).all()
        assert len(jobs) == int(accepted)
        events = db.scalars(select(ProfileAuditEvent).where(
            ProfileAuditEvent.object_id == pid, ProfileAuditEvent.action == "revoke")).all()
        assert [event.result for event in events] == ["accepted" if accepted else "denied"]
    if not accepted:
        assert not worker.step()
        assert pki.calls == calls
        assert not pki.revoked


def test_existing_revoke_replays_and_runs_after_expiry(scene, db_factory):
    client, headers, _, _, pki = scene
    created = create(client, headers, payload())
    worker = ProfileWorker(Settings(), db_factory, pki)
    assert worker.step()
    pid = uuid.UUID(created["profile"]["id"])
    key = {"idempotency_key": str(uuid.uuid4())}
    first = client.post(f"/api/v1/profiles/{pid}/revoke", json=key, headers=headers)
    assert first.status_code == 202
    with db_factory() as db:
        row = db.get(VpnProfile, pid)
        row.created_at = utcnow() - timedelta(days=2)
        row.expires_at = utcnow() - timedelta(days=1)
        db.commit()
    for completed in (False, True):
        if completed:
            assert worker.step()
        for value in (key, {"idempotency_key": str(uuid.uuid4())}):
            repeated = client.post(f"/api/v1/profiles/{pid}/revoke", json=value, headers=headers)
            assert repeated.status_code == 202
            assert repeated.json()["id"] == first.json()["id"]
    assert len(pki.revoked) == 1
    assert client.get(f"/api/v1/profiles/{pid}").json()["status"] == "revoking"


def test_create_idempotency_defaults_explicit_date_and_duration(scene, db_factory):
    client, headers, _, _, pki = scene
    data = payload()
    first = create(client, headers, data)
    assert create(client, headers, data) == first
    assert client.post("/api/v1/profiles", json={**data, "mode": "aws-direct"}, headers=headers).status_code == 409
    pid = uuid.UUID(first["profile"]["id"])
    with db_factory() as db:
        row = db.get(VpnProfile, pid)
        assert as_utc(row.expires_at) - as_utc(row.created_at) == timedelta(days=365)
        assert len(db.scalars(select(ProfileJob)).all()) == 1
    explicit = (utcnow() + timedelta(days=2)).replace(microsecond=0).isoformat()
    assert create(client, headers, payload(expires_at=explicit))["profile"]["expires_at"]
    duration = create(client, headers, payload(duration_days=7))
    with db_factory() as db:
        row = db.get(VpnProfile, uuid.UUID(duration["profile"]["id"]))
        assert as_utc(row.expires_at) - as_utc(row.created_at) == timedelta(days=7)


@pytest.mark.parametrize("extra", [{"duration_days": 1, "expires_at": "2027-01-01T00:00:00Z"}, {"duration_days": 0},
    {"duration_days": True}, {"duration_days": 10**15}, {"expires_at": "2027-01-01T00:00:00"}, {"expires_at": "2000-01-01T00:00:00Z"},
    {"role": "ADMIN"}, {"status": "active"}, {"id": str(uuid.uuid4())}, {"certificate_serial": "1"},
    {"device_name": "\r\nx"}, {"device_name": " "}, {"mode": "server"}])
def test_validation_and_mass_assignment_rejected(scene, extra):
    client, headers, *_ = scene
    assert client.post("/api/v1/profiles", json=payload(**extra), headers=headers).status_code == 422


def test_csrf_and_owner_registration_expiry_pki_outage(scene, db_factory):
    client, headers, owners, admin, pki = scene
    for owner in (uuid.uuid4(), admin, owners[2]):
        assert client.post("/api/v1/profiles", json=payload(owner), headers=headers).status_code == 422
    assert client.post("/api/v1/profiles", json=payload()).status_code == 403
    data = create(client, headers, payload())
    pid = data["profile"]["id"]
    for path, body in ((f"/api/v1/profiles/{pid}/owner", {"owner_id": str(owners[0])}),
                       (f"/api/v1/profiles/{pid}/revoke", {"idempotency_key": str(uuid.uuid4())})):
        assert client.post(path, json=body).status_code == 403
    assert client.patch(f"/api/v1/profiles/{pid}", json={"device_name": "x"}).status_code == 403
    ProfileWorker(Settings(), db_factory, pki).step()
    pki.error = "unavailable"
    response = client.post(f"/api/v1/profiles/{pid}/download", headers=headers)
    assert response.status_code == 503 and response.headers["cache-control"] == "no-store"
    with db_factory() as db:
        row = db.get(VpnProfile, uuid.UUID(pid))
        row.created_at = utcnow() - timedelta(days=3)
        row.expires_at = utcnow() - timedelta(days=1)
        db.commit()
    assert client.get(f"/api/v1/profiles/{pid}").json()["status"] == "expired"
    assert client.post(f"/api/v1/profiles/{pid}/download", headers=headers).status_code == 409


@pytest.mark.parametrize("point", ["after_pki", "before_db_commit"])
def test_worker_recovers_unknown_outcome_with_same_key(scene, db_factory, point):
    client, headers, _, _, pki = scene
    data = create(client, headers, payload())
    def crash(reached):
        if point == reached:
            raise RuntimeError("simulated worker loss")
    worker = ProfileWorker(Settings(), db_factory, pki, fault=crash)
    with pytest.raises(RuntimeError):
        worker.step()
    assert len(pki.issued) == 1
    with db_factory() as db:
        job = db.get(ProfileJob, uuid.UUID(data["job"]["id"]))
        assert job.status == "running"
        after_lease = as_utc(job.lease_until) + timedelta(seconds=1)
    recovered = ProfileWorker(Settings(), db_factory, pki)
    assert recovered.step(now=after_lease)
    assert len(pki.issued) == 1
    assert pki.calls[0][1] == pki.calls[1][1]
    assert client.get(f"/api/v1/profile-jobs/{data['job']['id']}").json()["status"] == "succeeded"


@pytest.mark.parametrize("code,expected", [("unavailable", "queued"), ("operation_failed", "queued"),
    ("storage_error", "queued"), ("expired", "failed"), ("conflict", "needs_review")])
def test_worker_failures_are_visible_and_retry_transient_outages(scene, db_factory, code, expected):
    client, headers, _, _, pki = scene
    data = create(client, headers, payload())
    pki.error = code
    worker = ProfileWorker(Settings(), db_factory, pki)
    assert worker.step()
    response = client.get(f"/api/v1/profile-jobs/{data['job']['id']}").json()
    assert response["status"] == expected
    assert response["error_code"].startswith("pki_")
    assert not worker.step()
    if expected == "queued":
        with db_factory() as db:
            job = db.get(ProfileJob, uuid.UUID(data["job"]["id"]))
            retry = as_utc(job.retry_at) + timedelta(seconds=1)
        pki.error = None
        assert worker.step(now=retry)
        assert client.get(f"/api/v1/profile-jobs/{data['job']['id']}").json()["status"] == "succeeded"


def test_profile_worker_runs_in_application_lifespan_and_stops(scene, db_factory, monkeypatch):
    import asyncio
    from veilway_control import main
    client, headers, _, _, pki = scene
    created = create(client, headers, payload())
    job_id = uuid.UUID(created["job"]["id"])
    class QuietRestartWorker:
        def __init__(self, *_):
            self.stopping = asyncio.Event()
        async def run(self):
            await self.stopping.wait()
        def stop(self):
            self.stopping.set()
    monkeypatch.setattr(main, "get_settings", lambda: Settings(worker_interval_seconds=1))
    monkeypatch.setattr(main, "get_session_factory", lambda: db_factory)
    monkeypatch.setattr(main, "RestartWorker", QuietRestartWorker)
    monkeypatch.setattr(main, "CrlWorker", QuietRestartWorker)
    monkeypatch.setattr(main, "ProfileWorker", lambda settings, factory: ProfileWorker(settings, factory, pki))
    async def run():
        async with main.lifespan(main.app):
            for _ in range(100):
                await asyncio.sleep(0.01)
                with db_factory() as db:
                    if db.get(ProfileJob, job_id).status == "succeeded":
                        break
            else:
                pytest.fail("application did not run its profile worker")
        assert main.app.state.profile_worker.stopping.is_set()
    asyncio.run(run())
