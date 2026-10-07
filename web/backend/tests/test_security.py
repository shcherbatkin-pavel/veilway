from __future__ import annotations

import io
import json
import sys

from sqlalchemy import select

from veilway_control import cli
from veilway_control.config import Settings
from veilway_control.models import RestartJob, User, UserSession
from veilway_control.security import (
    PASSWORD_HASHER,
    create_user_session,
    hash_token,
    verify_password,
)


def test_argon2_password_and_token_hashing() -> None:
    password = "correct horse battery staple"
    encoded = PASSWORD_HASHER.hash(password)

    assert encoded.startswith("$argon2id$")
    assert verify_password(encoded, password)
    assert not verify_password(encoded, "wrong password")
    assert hash_token("token") != b"token"


def test_bootstrap_admin_reads_password_from_stdin_and_revokes_sessions(
    db_factory, monkeypatch
) -> None:
    monkeypatch.setattr(cli, "get_session_factory", lambda: db_factory)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            '{"login":"operator","password":"correct horse battery staple"}'
        ),
    )
    cli.bootstrap_admin()

    with db_factory() as db:
        admin = db.scalar(select(User))
        assert admin is not None
        assert admin.password_hash != "correct horse battery staple"
        assert verify_password(admin.password_hash, "correct horse battery staple")
        assert db.scalars(select(UserSession)).all() == []

        original_hash = admin.password_hash
        create_user_session(db, admin, Settings(public_host="testserver"))

    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            '{"login":"operator","password":"correct horse battery staple"}'
        ),
    )
    cli.bootstrap_admin()
    with db_factory() as db:
        admin = db.scalar(select(User))
        assert admin is not None
        assert admin.password_hash == original_hash
        assert len(db.scalars(select(UserSession)).all()) == 1


def test_bootstrap_preserves_history_and_google_users(db_factory, seed_control_data, monkeypatch):
    ids = seed_control_data(legacy_admin=True)
    monkeypatch.setattr(cli, "get_session_factory", lambda: db_factory)
    with db_factory() as db:
        legacy = db.get(User, ids["user_id"])
        create_user_session(db, legacy, Settings(public_host="testserver"))
        google = User(google_sub="test-google-sub", email="operator@example.invalid")
        db.add(google)
        db.flush()
        google_id = google.id
        create_user_session(db, google, Settings(public_host="testserver"))
        job = RestartJob(user_id=legacy.id, status="succeeded")
        db.add(job)
        db.commit()
        job_id = job.id
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "login": "new-operator", "password": "correct horse battery staple",
    })))
    cli.bootstrap_admin()
    with db_factory() as db:
        legacy = db.get(User, ids["user_id"])
        assert legacy is not None and not legacy.is_active
        assert legacy.google_sub is None and legacy.email is None
        assert db.get(RestartJob, job_id).user_id == legacy.id
        google = db.get(User, google_id)
        assert google.google_sub == "test-google-sub" and google.role == "USER" and google.is_active
        assert [s.user_id for s in db.scalars(select(UserSession))] == [google_id]
        replacement = db.scalar(select(User).where(User.login == "new-operator"))
        assert replacement.role == "ADMIN" and replacement.is_active
