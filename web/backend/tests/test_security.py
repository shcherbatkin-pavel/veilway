from __future__ import annotations

import io
import sys

from sqlalchemy import select

from veilway_control import cli
from veilway_control.config import Settings
from veilway_control.models import Admin, AdminSession
from veilway_control.security import (
    PASSWORD_HASHER,
    create_admin_session,
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
        admin = db.scalar(select(Admin))
        assert admin is not None
        assert admin.password_hash != "correct horse battery staple"
        assert verify_password(admin.password_hash, "correct horse battery staple")
        assert db.scalars(select(AdminSession)).all() == []

        original_hash = admin.password_hash
        create_admin_session(db, admin, Settings(public_host="testserver"))

    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            '{"login":"operator","password":"correct horse battery staple"}'
        ),
    )
    cli.bootstrap_admin()
    with db_factory() as db:
        admin = db.scalar(select(Admin))
        assert admin is not None
        assert admin.password_hash == original_hash
        assert len(db.scalars(select(AdminSession)).all()) == 1
