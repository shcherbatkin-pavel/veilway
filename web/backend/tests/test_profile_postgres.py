"""Concurrency and recovery against explicitly supplied disposable PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
import uuid

import pytest
from alembic import command
from fastapi import HTTPException
from sqlalchemy import select
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from test_migrations import postgres_connection, config
from test_profiles import FakePki
from veilway_control.config import Settings
from veilway_control.models import ProfileAuditEvent, ProfileJob, User, VpnProfile, as_utc, utcnow
from veilway_control.profile_worker import ProfileWorker
from veilway_control.profiles import assign_profile, create_profile, revoke_profile
from veilway_control.schemas import ProfileCreateRequest


@pytest.fixture
def profile_db(postgres_connection):
    connection = postgres_connection
    command.upgrade(config(connection), "head")
    connection.commit()
    schema = connection.scalar(sa.text('SELECT current_schema()'))
    engine = sa.create_engine(connection.engine.url,
        connect_args={"options": f"-csearch_path={schema}"})
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        admin = User(role="ADMIN", google_sub="profile-pg-admin", email="admin@example.test")
        users = [User(role="USER", google_sub=f"profile-pg-{index}", email=f"owner{index}@example.test") for index in range(2)]
        db.add_all([admin, *users])
        db.commit()
        ids = (admin.id, users[0].id, users[1].id)
    yield factory, ids
    engine.dispose()


def request(owner=None, **values):
    return ProfileCreateRequest(idempotency_key=uuid.uuid4(), device_name="Laptop", mode="aws-direct", owner_id=owner, duration_days=3, **values)


def test_concurrent_duplicate_create_commits_one_profile_job_and_audit(profile_db):
    factory, (admin_id, owner, _) = profile_db
    payload = request(owner)
    barrier = Barrier(4)
    def create(_):
        with factory() as db:
            admin = db.get(User, admin_id)
            barrier.wait(timeout=10)
            profile, job = create_profile(db, admin, payload)
            return profile.id, job.id
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(create, range(4)))
    assert len(set(results)) == 1
    with factory() as db:
        assert len(db.scalars(select(VpnProfile)).all()) == 1
        assert len(db.scalars(select(ProfileJob)).all()) == 1
        assert len(db.scalars(select(ProfileAuditEvent)).all()) == 1


def test_competing_owner_assignments_cannot_transfer_profile(profile_db):
    factory, (admin_id, owner1, owner2) = profile_db
    with factory() as db:
        profile, _ = create_profile(db, db.get(User, admin_id), request())
        pid = profile.id
    barrier = Barrier(2)
    def assign(owner):
        with factory() as db:
            admin = db.get(User, admin_id)
            barrier.wait(timeout=10)
            try:
                assign_profile(db, admin, pid, owner)
                return owner, 200
            except HTTPException as error:
                return owner, error.status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(assign, (owner1, owner2)))
    assert sorted(code for _, code in results) == [200, 409]
    winner = next(owner for owner, code in results if code == 200)
    with factory() as db:
        assert db.get(VpnProfile, pid).owner_id == winner


def test_workers_claim_once_and_concurrent_revoke_keeps_one_job(profile_db):
    factory, (admin_id, owner, _) = profile_db
    with factory() as db:
        profile, _ = create_profile(db, db.get(User, admin_id), request(owner))
        pid = profile.id
    pki = FakePki()
    barrier = Barrier(4)
    def run(_):
        barrier.wait(timeout=10)
        return ProfileWorker(Settings(), factory, pki).step()
    with ThreadPoolExecutor(max_workers=4) as executor:
        result = list(executor.map(run, range(4)))
    assert sum(result) == 1
    assert len(pki.issued) == 1
    barrier = Barrier(4)
    def revoke(_):
        with factory() as db:
            admin = db.get(User, admin_id)
            barrier.wait(timeout=10)
            return revoke_profile(db, admin, pid, uuid.uuid4()).id
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(revoke, range(4)))
    assert len(set(results)) == 1
    assert ProfileWorker(Settings(), factory, pki).step()
    with factory() as db:
        assert db.get(VpnProfile, pid).status == "revoking"
        job = db.get(ProfileJob, results[0])
        assert job.status == "succeeded" and job.crl_number is not None


@pytest.mark.parametrize("point", ["after_pki", "before_db_commit"])
def test_lost_worker_replays_same_key_without_new_certificate(profile_db, point):
    factory, (admin_id, owner, _) = profile_db
    with factory() as db:
        profile, job = create_profile(db, db.get(User, admin_id), request(owner))
        job_id = job.id
    pki = FakePki()
    def crash(reached):
        if reached == point:
            raise RuntimeError("synthetic failure")
    with pytest.raises(RuntimeError):
        ProfileWorker(Settings(), factory, pki, fault=crash).step()
    with factory() as db:
        job = db.get(ProfileJob, job_id)
        assert job.status == "running"
        after = as_utc(job.lease_until) + timedelta(seconds=1)
    assert ProfileWorker(Settings(), factory, pki).step(now=after)
    assert len(pki.issued) == 1
    assert len({key for _, key in pki.calls}) == 1
    with factory() as db:
        assert db.get(ProfileJob, job_id).status == "succeeded"


def test_migration_downgrade_preserves_active_jobs_and_audit(profile_db):
    factory, (admin_id, owner, _) = profile_db
    with factory() as db:
        create_profile(db, db.get(User, admin_id), request(owner))
    with factory.kw['bind'].connect() as connection:
        with pytest.raises(RuntimeError, match="operator-preserving"):
            command.downgrade(config(connection), "0003_google_sign_in")
        connection.rollback()
    with factory() as db:
        assert len(db.scalars(select(ProfileAuditEvent)).all()) == 1
        assert len(db.scalars(select(ProfileJob)).all()) == 1


def test_expired_lease_fences_late_worker_completion(profile_db):
    from threading import Event
    factory, (admin_id, owner, _) = profile_db
    with factory() as db:
        profile, job = create_profile(db, db.get(User, admin_id), request(owner))
        job_id = job.id
    entered, release = Event(), Event()
    class DelayedPki(FakePki):
        def issue(self, *arguments):
            result = super().issue(*arguments)
            if len(self.calls) == 1:
                entered.set()
                assert release.wait(timeout=20)
            return result
    pki = DelayedPki()
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(ProfileWorker(Settings(), factory, pki).step)
        assert entered.wait(timeout=10)
        try:
            with factory() as db:
                job = db.get(ProfileJob, job_id)
                after = as_utc(job.lease_until) + timedelta(seconds=1)
            assert ProfileWorker(Settings(), factory, pki).step(now=after)
        finally:
            release.set()
        assert first.result(timeout=10)
    with factory() as db:
        job = db.get(ProfileJob, job_id)
        assert job.status == "succeeded" and job.attempts == 2
        events = db.scalars(select(ProfileAuditEvent).where(ProfileAuditEvent.action == "issue_result")).all()
        assert len(events) == 1
    assert len(pki.issued) == 1
