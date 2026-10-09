from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from test_api import build_client, login
from veilway_control.access import get_visible_profile, registered_users, visible_jobs, visible_profiles
from veilway_control.config import Settings
from veilway_control.models import ProfileJob, RestartJob, User, UserSession, VpnProfile, utcnow
from veilway_control.security import create_user_session
from veilway_control.services import create_restart
from veilway_control.profiles import registered_owner


def google_user(subject: str, *, role: str = "USER") -> User:
    return User(google_sub=subject, email=f"{subject}@example.invalid", role=role)


def seed_user_session(db_factory, *, role="USER"):
    with db_factory() as db:
        user = google_user(str(uuid.uuid4()), role=role)
        db.add(user)
        db.flush()
        _, token, csrf = create_user_session(db, user, Settings(public_host="testserver"))
        return user.id, token, csrf


@pytest.mark.parametrize("role,expected", [(None, 401), ("USER", 403), ("ADMIN", 200)])
def test_infrastructure_access_matrix(db_factory, seed_control_data, role, expected):
    ids = seed_control_data()
    with db_factory() as db:
        historical = RestartJob(user_id=ids["user_id"], status="succeeded")
        db.add(historical)
        db.commit()
        historical_id = historical.id
    with build_client(db_factory) as client:
        csrf = "unused"
        if role is not None:
            _, token, csrf = seed_user_session(db_factory, role=role)
            client.cookies.set("__Host-veilway_session", token)
        for path, admin_status in (
            ("/vpn-vms", 200), ("/restart-jobs", 200),
            (f"/restart-jobs/{historical_id}", 200), (f"/restart-jobs/{uuid.uuid4()}", 404),
        ):
            response = client.get(f"/api/v1{path}")
            assert response.status_code == (admin_status if role == "ADMIN" else expected)
            if expected != 200:
                assert response.json() == {"detail": "Unauthorized" if expected == 401 else "Forbidden"}
        response = client.post(
            "/api/v1/restart-jobs", json={"targets": ["aws-direct"]},
            headers={"X-CSRF-Token": csrf, "X-Role": "ADMIN"},
        )
        assert response.status_code == (202 if role == "ADMIN" else expected)
    with db_factory() as db:
        assert len(db.scalars(select(RestartJob)).all()) == (2 if role == "ADMIN" else 1)


def test_user_can_read_session_and_logout_but_cannot_change_role(db_factory):
    user_id, token, _ = seed_user_session(db_factory)
    with build_client(db_factory) as client:
        client.cookies.set("__Host-veilway_session", token)
        response = client.get("/api/v1/auth/session")
        assert response.status_code == 200
        csrf = response.json()["csrf_token"]
        assert client.post("/api/v1/auth/logout").status_code == 403
        assert client.patch(
            f"/api/v1/users/{user_id}", json={"role": "ADMIN"},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 404
        assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
        assert client.get("/api/v1/auth/session").status_code == 401
    with db_factory() as db:
        assert db.get(User, user_id).role == "USER"


def test_password_login_is_disabled_and_cannot_mass_assign_role(db_factory, seed_control_data):
    ids = seed_control_data()
    with build_client(db_factory) as client:
        response = client.post("/api/v1/auth/login", json={
            "login": "operator", "password": "correct horse battery staple", "role": "USER",
        })
        assert response.status_code == 404
        assert client.get("/api/v1/vpn-vms").status_code == 401
        login(client)
        assert client.get("/api/v1/vpn-vms").status_code == 200
    with db_factory() as db:
        assert db.get(User, ids["user_id"]).role == "ADMIN"


def test_role_and_active_status_are_checked_on_every_request(db_factory, seed_control_data):
    ids = seed_control_data()
    with build_client(db_factory) as client:
        csrf = login(client)
        with db_factory() as db:
            db.get(User, ids["user_id"]).role = "USER"
            db.commit()
        assert client.get("/api/v1/vpn-vms").status_code == 403
        assert client.post("/api/v1/restart-jobs", json={"targets": ["aws-direct"]},
                           headers={"X-CSRF-Token": csrf}).status_code == 403
        with db_factory() as db:
            db.get(User, ids["user_id"]).is_active = False
            db.commit()
        assert client.get("/api/v1/auth/session").status_code == 401


def test_profile_ownership_matrix(db_factory, seed_control_data):
    ids = seed_control_data()
    with db_factory() as db:
        admin = db.get(User, ids["user_id"])
        owner, stranger = google_user("owner"), google_user("stranger")
        db.add_all([owner, stranger])
        db.flush()
        profiles = [VpnProfile(
            device_name="Test device", mode=mode, owner_id=user_id,
            created_by_id=admin.id, expires_at=utcnow() + timedelta(days=365),
        ) for mode, user_id in (
            ("yc-direct", owner.id), ("aws-direct", stranger.id), ("yc-aws-multihop", None),
        )]
        db.add_all(profiles)
        db.commit()
        assert db.scalars(visible_profiles(admin)).all() == profiles
        assert db.scalars(visible_profiles(owner)).all() == [profiles[0]]
        assert db.scalars(visible_profiles(stranger)).all() == [profiles[1]]
        assert get_visible_profile(db, owner, profiles[0].id) is profiles[0]
        for profile_id in (profiles[1].id, profiles[2].id, uuid.uuid4()):
            with pytest.raises(HTTPException) as denied:
                get_visible_profile(db, owner, profile_id)
            assert denied.value.status_code == 404
        for profile in profiles:
            assert get_visible_profile(db, admin, profile.id) is profile
        jobs = [ProfileJob(profile_id=profile.id, requested_by_id=admin.id, kind="issue") for profile in profiles]
        db.add_all(jobs)
        db.commit()
        assert db.scalars(visible_jobs(admin)).all() == jobs
        assert db.scalars(visible_jobs(owner)).all() == [jobs[0]]
        assert db.scalars(visible_jobs(stranger)).all() == [jobs[1]]
        assert db.scalar(visible_jobs(owner).where(ProfileJob.id == jobs[1].id)) is None
        assert db.scalar(visible_jobs(owner).where(ProfileJob.id == jobs[2].id)) is None
        owner.is_active = False
        with pytest.raises(HTTPException) as denied:
            visible_profiles(owner)
        assert denied.value.status_code == 401


@pytest.mark.parametrize("query", [visible_profiles, visible_jobs])
@pytest.mark.parametrize("active,role,status", [
    (False, "USER", 401), (False, "ADMIN", 401),
    (False, "UNKNOWN", 401), (True, "UNKNOWN", 403),
])
def test_metadata_queries_reject_inactive_users_and_unknown_roles(query, active, role, status):
    # Invalid roles cannot be persisted; test the query boundary directly.
    user = User(id=uuid.uuid4(), role=role, is_active=active)
    with pytest.raises(HTTPException) as denied:
        query(user)
    assert denied.value.status_code == status


def test_user_listing_and_owner_validation_share_registration_rules(db_factory, seed_control_data):
    seed_control_data()
    with db_factory() as db:
        eligible = [google_user("zeta"), google_user("alpha")]
        inactive = google_user("inactive")
        inactive.is_active = False
        admin = google_user("another-admin", role="ADMIN")
        legacy = User(login="legacy-user", password_hash="synthetic-test-only-hash", role="USER")
        db.add_all([*eligible, inactive, admin, legacy])
        db.commit()
        assert db.scalars(registered_users().order_by(User.email)).all() == list(reversed(eligible))
        for user in eligible:
            registered_owner(db, user.id)
        registered_owner(db, None)
        for owner_id in (inactive.id, admin.id, legacy.id, uuid.uuid4()):
            with pytest.raises(HTTPException) as denied:
                registered_owner(db, owner_id)
            assert denied.value.status_code == 422
            assert denied.value.detail == "owner must be a registered active USER"
        expected = [{"id": str(user.id), "email": user.email} for user in reversed(eligible)]
    with build_client(db_factory) as client:
        login(client)
        assert client.get("/api/v1/users").json() == expected
        assert client.get("/api/v1/users?limit=1&offset=1").json() == expected[1:]


def test_restart_service_also_rejects_user(db_factory, seed_control_data):
    seed_control_data()
    user_id, _, _ = seed_user_session(db_factory)
    with db_factory() as db:
        with pytest.raises(HTTPException) as denied:
            create_restart(db, Settings(public_host="testserver"), user_id, ["aws-direct"], now=utcnow())
        assert denied.value.status_code == 403
        assert db.scalars(select(RestartJob)).all() == []


def test_google_subject_is_unique_and_defaults_to_user(db_factory):
    with db_factory() as db:
        first = User(google_sub="subject", email="test@example.invalid")
        db.add(first)
        db.commit()
        assert first.role == "USER"
        db.add(User(google_sub="subject", email="different@example.invalid"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        # The same email alone must never merge two distinct Google identities.
        db.add(User(google_sub="different-subject", email=first.email))
        db.commit()


@pytest.mark.parametrize("values", [
    {"role": "OWNER", "google_sub": "subject", "email": "test@example.invalid"},
    {"google_sub": "subject"},
    {"google_sub": "subject", "email": "test@example.invalid", "login": "operator", "password_hash": "test-only"},
])
def test_database_rejects_invalid_identity_or_role(db_factory, values):
    with db_factory() as db:
        db.add(User(**values))
        with pytest.raises(IntegrityError):
            db.commit()


def test_profile_jobs_foreign_keys_and_idempotency(db_factory, seed_control_data):
    ids = seed_control_data()
    with db_factory() as db:
        profile = VpnProfile(device_name="Test", mode="yc-direct", created_by_id=ids["user_id"],
                             expires_at=utcnow() + timedelta(days=1))
        db.add(profile)
        db.flush()
        job = ProfileJob(profile_id=profile.id, requested_by_id=ids["user_id"], kind="issue")
        db.add(job)
        db.commit()
        assert job.status == "queued" and job.profile is profile
        db.add(ProfileJob(profile_id=profile.id, requested_by_id=ids["user_id"], kind="revoke",
                          idempotency_key=job.idempotency_key))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.delete(db.get(User, ids["user_id"]))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert db.get(VpnProfile, profile.id) is not None
        assert db.get(ProfileJob, job.id) is not None


def test_expired_and_missing_sessions_are_rejected(db_factory):
    _, token, _ = seed_user_session(db_factory)
    with db_factory() as db:
        db.scalar(select(UserSession)).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    with build_client(db_factory) as client:
        client.cookies.set("__Host-veilway_session", token)
        assert client.get("/api/v1/auth/session").status_code == 401
        client.cookies.set("__Host-veilway_session", "unknown-test-token")
        assert client.get("/api/v1/auth/session").status_code == 401
