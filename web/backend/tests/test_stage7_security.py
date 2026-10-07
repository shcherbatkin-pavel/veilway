"""Cross-cutting acceptance using disposable identities and synthetic material."""
import asyncio
import uuid

import pytest
from sqlalchemy import select

from test_profiles import scene, payload, create, user_client
from privacy_helpers import assert_database_excludes
from veilway_control.config import Settings
from veilway_control.models import ProfileJob, User, VpnProfile
from veilway_control.profile_worker import ProfileWorker


def test_denials_validation_and_success_are_never_cacheable(scene):
    client, headers, owners, _, _ = scene
    pid = create(client, headers, payload())["profile"]["id"]
    requests = [
        ("get", "/profiles", None, headers, 200),
        ("get", f"/profiles/{uuid.uuid4()}", None, headers, 404),
        ("post", "/profiles", payload(), {}, 403),
        ("post", "/profiles", payload(status="active"), headers, 422),
        ("post", f"/profiles/{pid}/download", None, headers, 409),
    ]
    for method, path, body, supplied, expected in requests:
        response = client.request(method, "/api/v1" + path, json=body, headers=supplied)
        assert response.status_code == expected
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
    client.cookies.clear()
    response = client.get("/api/v1/auth/session")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


def test_rejected_sensitive_input_is_not_echoed_or_persisted(scene, db_factory, caplog):
    client, headers, *_ = scene
    marker = "synthetic-private-profile-pasted-by-mistake"
    response = client.post("/api/v1/profiles", json=payload(private_key=marker), headers=headers)
    assert response.status_code == 422 and response.json() == {"detail": "invalid request"}
    assert marker not in response.text + caplog.text
    assert_database_excludes(db_factory, [marker])


@pytest.mark.parametrize("operation", ["rename", "assign", "revoke"])
def test_mutation_cannot_mass_assign_profile_fields(scene, db_factory, operation):
    client, headers, owners, _, _ = scene
    pid = create(client, headers, payload())["profile"]["id"]
    path, method, body = {
        "rename": (f"/profiles/{pid}", "PATCH", {"device_name": "New", "owner_id": str(owners[0])}),
        "assign": (f"/profiles/{pid}/owner", "POST", {"owner_id": str(owners[0]), "status": "active"}),
        "revoke": (f"/profiles/{pid}/revoke", "POST", {"idempotency_key": str(uuid.uuid4()), "certificate_serial": "1"}),
    }[operation]
    assert client.request(method, "/api/v1" + path, json=body, headers=headers).status_code == 422
    with db_factory() as db:
        profile = db.get(VpnProfile, uuid.UUID(pid))
        assert profile.device_name == "Laptop" and profile.owner_id is None
        assert profile.status == "issuing" and profile.certificate_serial is None
        assert len(db.scalars(select(ProfileJob)).all()) == 1


def test_csrf_is_bound_to_session_and_client_cannot_assert_admin(scene, db_factory):
    admin, admin_headers, owners, _, pki = scene
    created = create(admin, admin_headers, payload(owners[0]))
    pid = created["profile"]["id"]
    assert ProfileWorker(Settings(), db_factory, pki).step()
    user, user_headers = user_client(db_factory, owners[0], pki)
    with user:
        forged = {**admin_headers, "X-Role": "ADMIN", "X-User-Id": str(owners[1])}
        assert user.post(f"/api/v1/profiles/{pid}/download", headers=forged).status_code == 403
        forged = {**user_headers, "X-Role": "ADMIN"}
        assert user.post("/api/v1/profiles", json=payload(), headers=forged).status_code == 403
        assert user.get("/api/v1/users", headers=forged).status_code == 403
        # A valid owner token remains usable; swapping CSRF never invalidates it.
        assert user.post(f"/api/v1/profiles/{pid}/download", headers=user_headers).status_code == 200
        with db_factory() as db:
            db.get(User, owners[0]).is_active = False
            db.commit()
        assert user.post(f"/api/v1/profiles/{pid}/download", headers=user_headers).status_code == 401


def test_profile_worker_survives_database_outage_without_logging_secret(scene, db_factory, caplog):
    client, headers, _, _, pki = scene
    created = create(client, headers, payload())
    calls = 0
    marker = "synthetic-database-password-never-log"

    def intermittent_database():
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise RuntimeError(marker)
        return db_factory()

    worker = ProfileWorker(Settings(worker_interval_seconds=1), intermittent_database, pki)

    async def exercise():
        task = asyncio.create_task(worker.run())
        try:
            for _ in range(250):
                await asyncio.sleep(0.02)
                with db_factory() as db:
                    if db.get(ProfileJob, uuid.UUID(created["job"]["id"])).status == "succeeded":
                        break
            else:
                pytest.fail("worker did not recover after database outage")
        finally:
            worker.stop()
            await asyncio.wait_for(task, 5)

    asyncio.run(exercise())
    assert calls >= 3 and len(pki.issued) == 1
    assert marker not in caplog.text
    assert_database_excludes(db_factory, [marker])
