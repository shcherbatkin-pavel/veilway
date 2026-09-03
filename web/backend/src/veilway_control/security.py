from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import get_db
from .models import Admin, AdminSession, as_utc, utcnow


PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=2,
    hash_len=32,
    salt_len=16,
)
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("veilway-invalid-login-placeholder")


def hash_token(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerifyMismatchError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class AuthenticatedAdmin:
    admin: Admin
    session: AdminSession


def create_admin_session(
    db: Session, admin: Admin, settings: Settings
) -> tuple[AdminSession, str, str]:
    session_token = new_token()
    csrf_token = new_token()
    now = utcnow()
    db.execute(
        delete(AdminSession).where(
            (AdminSession.admin_id == admin.id) & (AdminSession.expires_at <= now)
        )
    )
    session = AdminSession(
        admin_id=admin.id,
        token_hash=hash_token(session_token),
        csrf_hash=hash_token(csrf_token),
        expires_at=now + timedelta(hours=settings.session_hours),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session, session_token, csrf_token


def authenticate_admin(
    session_cookie: str | None = Cookie(default=None, alias="__Host-veilway_session"),
    db: Session = Depends(get_db),
) -> AuthenticatedAdmin:
    if not session_cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    token_digest = hash_token(session_cookie)
    session = db.scalar(
        select(AdminSession).where(AdminSession.token_hash == token_digest)
    )
    now = datetime.now(timezone.utc)
    if session is None or as_utc(session.expires_at) <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    admin = db.get(Admin, session.admin_id)
    if admin is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    session.last_seen_at = now
    db.commit()
    return AuthenticatedAdmin(admin=admin, session=session)


def require_csrf(
    authenticated: AuthenticatedAdmin = Depends(authenticate_admin),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> AuthenticatedAdmin:
    if not csrf_token or not hmac.compare_digest(
        authenticated.session.csrf_hash, hash_token(csrf_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid CSRF token",
        )
    return authenticated
