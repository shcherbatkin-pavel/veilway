from datetime import timedelta
import uuid

import pytest
from sqlalchemy import select

from test_api import build_client, login
from test_profiles import FakePki
from veilway_control.config import Settings
from veilway_control.mobile_models import MobileDeviceAttempt, MobileSession
from veilway_control.models import User, VpnProfile, ProfileAuditEvent, utcnow
from veilway_control.profile_api import get_pki_client
from veilway_control.security import create_user_session, hash_token

PREFIX = "/api/v1/mobile"


@pytest.fixture
def scene(db_factory, seed_control_data):
    ids = seed_control_data()
    pki = FakePki()
    with build_client(db_factory) as client:
        client.app.dependency_overrides[get_pki_client] = lambda: pki
        headers = {"X-CSRF-Token": login(client)}
        yield client, headers, ids["user_id"], pki


def advance(db_factory, code, **values):
    with db_factory() as db:
        attempt = db.scalar(select(MobileDeviceAttempt).where(MobileDeviceAttempt.device_hash == hash_token(code)))
        attempt.next_poll_at = utcnow() - timedelta(seconds=1)
        for key, value in values.items():
            setattr(attempt, key, value)
        db.commit()


def authorize(scene, db_factory):
    client, headers, user_id, _ = scene
    value = client.post(PREFIX + "/auth/device").json()
    assert client.post(PREFIX + "/auth/approve", headers=headers, json={"user_code": value["user_code"]}).status_code == 204
    advance(db_factory, value["device_code"])
    response = client.post(PREFIX + "/auth/token", json={"device_code": value["device_code"]})
    assert response.status_code == 200
    return value, {"Authorization": "Bearer " + response.json()["access_token"]}


def test_confirmation_polling_consumption_and_revocation(scene, db_factory):
    client, headers, user_id, _ = scene
    value = client.post(PREFIX + "/auth/device").json()
    assert value["expires_in"] == 600 and value["interval"] == 5
    payload = {"device_code": value["device_code"]}
    response = client.post(PREFIX + "/auth/token", json=payload)
    assert response.json() == {"error": "slow_down"}
    advance(db_factory, value["device_code"])
    assert client.post(PREFIX + "/auth/token", json=payload).json() == {"error": "authorization_pending"}
    assert client.post(PREFIX + "/auth/approve", json={"user_code": value["user_code"]}).status_code == 403
    assert client.post(PREFIX + "/auth/approve", headers=headers, json={"user_code": value["user_code"]}).status_code == 204
    assert client.post(PREFIX + "/auth/approve", headers=headers, json={"user_code": value["user_code"]}).status_code == 400
    advance(db_factory, value["device_code"])
    response = client.post(PREFIX + "/auth/token", json=payload)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    token = response.json()["access_token"]
    assert response.json()["expires_in"] == 30 * 86400
    with db_factory() as db:
        assert db.scalar(select(MobileSession)).token_hash == hash_token(token)
        assert db.scalar(select(MobileDeviceAttempt)) is None
    assert client.post(PREFIX + "/auth/token", json=payload).json() == {"error": "expired_token"}
    client.cookies.clear()
    auth = {"Authorization": "Bearer " + token}
    assert client.get(PREFIX + "/profiles", headers=auth).status_code == 200
    assert client.post(PREFIX + "/auth/logout", headers=auth).status_code == 204
    assert client.get(PREFIX + "/profiles", headers=auth).status_code == 401


def test_admin_token_is_owner_only_and_cannot_administer(scene, db_factory):
    client, _, admin_id, pki = scene
    _, auth = authorize(scene, db_factory)
    with db_factory() as db:
        owner = User(google_sub="mobile-owner", email="mobile@example.test", role="USER")
        db.add(owner); db.flush()
        own = VpnProfile(device_name="phone", created_by_id=admin_id, owner_id=admin_id, mode="yc-direct", status="active",
                         expires_at=utcnow() + timedelta(days=1))
        foreign = VpnProfile(device_name="other", created_by_id=admin_id, owner_id=owner.id, mode="yc-direct", status="active",
                             expires_at=utcnow() + timedelta(days=1))
        db.add_all([own, foreign]); db.commit()
        own_id, foreign_id = str(own.id), str(foreign.id)
    client.cookies.clear()
    assert [p["id"] for p in client.get(PREFIX + "/profiles", headers=auth).json()] == [own_id]
    assert client.post(PREFIX + f"/profiles/{foreign_id}/download", headers=auth).status_code == 404
    response = client.post(PREFIX + f"/profiles/{own_id}/download", headers=auth)
    assert response.content == b"synthetic test-only profile"
    assert response.headers["cache-control"] == "no-store"
    for path in ("/users", "/vpn-vms", "/profiles", "/auth/session"):
        assert client.get("/api/v1" + path, headers=auth).status_code == 401
    assert client.post("/api/v1/restart-jobs", headers=auth, json={"targets": ["aws-direct"]}).status_code == 401
    with db_factory() as db:
        assert db.scalar(select(ProfileAuditEvent)).action == "download"
        db.get(VpnProfile, uuid.UUID(own_id)).status = "revoking"; db.commit()
    assert client.post(PREFIX + f"/profiles/{own_id}/download", headers=auth).status_code == 409
    pki.error = "test-error"
    with db_factory() as db:
        db.get(VpnProfile, uuid.UUID(own_id)).status = "active"; db.commit()
    assert client.post(PREFIX + f"/profiles/{own_id}/download", headers=auth).status_code == 503


def test_expired_attempt_disabled_user_and_expired_session(scene, db_factory):
    client, headers, user_id, _ = scene
    value = client.post(PREFIX + "/auth/device").json()
    advance(db_factory, value["device_code"], expires_at=utcnow() - timedelta(seconds=1))
    assert client.post(PREFIX + "/auth/approve", headers=headers, json={"user_code": value["user_code"]}).status_code == 400
    assert client.post(PREFIX + "/auth/token", json={"device_code": value["device_code"]}).json()["error"] == "expired_token"
    _, auth = authorize(scene, db_factory)
    with db_factory() as db:
        db.scalar(select(MobileSession)).expires_at = utcnow() - timedelta(seconds=1); db.commit()
    assert client.get(PREFIX + "/profiles", headers=auth).status_code == 401
    _, auth = authorize(scene, db_factory)
    with db_factory() as db:
        db.get(User, user_id).is_active = False; db.commit()
    assert client.get(PREFIX + "/profiles", headers=auth).status_code == 401


def test_limits_are_shared_and_unknown_codes_do_not_leak(scene, db_factory):
    client, headers, _, _ = scene
    for _ in range(30):
        assert client.post(PREFIX + "/auth/device").status_code == 200
    assert client.post(PREFIX + "/auth/device").status_code == 429
    for _ in range(20):
        response = client.post(PREFIX + "/auth/approve", headers=headers, json={"user_code": "ABCD-2345"})
        assert response.status_code == 400
    assert client.post(PREFIX + "/auth/approve", headers=headers, json={"user_code": "ABCD-2345"}).status_code == 429
    assert client.post(PREFIX + "/auth/token", json={"device_code": "A" * 43}).json() == {"error": "expired_token"}
    assert client.get(PREFIX + "/profiles", headers={"Authorization": "Bearer bad"}).status_code == 401


def test_oversized_chunked_payload_rejected_before_parsing():
    import asyncio
    from veilway_control.analytics_body import AnalyticsBodyLimit
    called, sent = [], []
    async def app(scope, receive, send):
        called.append(True)
    messages = iter([{"type": "http.request", "body": b"x" * 3000, "more_body": True},
                     {"type": "http.request", "body": b"x" * 2000, "more_body": False}])
    async def receive():
        return next(messages)
    async def send(message):
        sent.append(message)
    asyncio.run(AnalyticsBodyLimit(app, limit=4096, prefix=PREFIX + "/", detail="mobile payload too large")(
        {"type": "http", "path": PREFIX + "/auth/token"}, receive, send))
    assert not called
    assert sent[0]["status"] == 413
    assert (b"cache-control", b"no-store") in sent[0]["headers"]
