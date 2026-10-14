"""Mobile auth scenarios with explicit, unchanged transaction boundaries."""
from __future__ import annotations
from dataclasses import dataclass
import re
import secrets
from datetime import timedelta
from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session
from .config import Settings
from .mobile_models import MobileDeviceAttempt, MobileRateBucket, MobileSession
from .models import User, as_utc, utcnow
from .security import hash_token, new_token

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}

class MobileFailure(Exception):
    def __init__(self, status: int, detail: str | None = None, headers: dict | None = None):
        self.status, self.detail, self.headers = status, detail, headers if headers is not None else NO_STORE
        super().__init__(detail or "unauthorized")

@dataclass(frozen=True)
class MobileIdentity:
    user: User
    session: MobileSession

@dataclass(frozen=True)
class TokenResult:
    payload: dict
    status: int = 200


def rate_limit(db: Session, key: str, limit: int, seconds: int) -> None:
    """Atomic, persistent fixed-window limiter shared by API workers.

    Public allocation/polling uses bounded global buckets, not attacker-controlled
    forwarded addresses. Confirmation is additionally limited by account.
    """
    window = int(utcnow().timestamp()) // seconds
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    statement = insert(MobileRateBucket).values(key=key, window=window, count=1)
    count = db.scalar(statement.on_conflict_do_update(index_elements=[MobileRateBucket.key], set_={
        "window": window,
        "count": case((MobileRateBucket.window == window, MobileRateBucket.count + 1), else_=1),
    }).returning(MobileRateBucket.count))
    db.commit()
    if count > limit:
        raise MobileFailure(429, "try again later", headers={**NO_STORE, "Retry-After": str(seconds)})


def allocate_device(db: Session, settings: Settings) -> dict:
    rate_limit(db, "device", 30, 60)
    now = utcnow()
    db.execute(delete(MobileDeviceAttempt).where(MobileDeviceAttempt.expires_at <= now))
    db.execute(delete(MobileSession).where(MobileSession.expires_at <= now))
    device_code = new_token()
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    value = "".join(secrets.choice(alphabet) for _ in range(8))
    user_code = value[:4] + "-" + value[4:]
    db.add(MobileDeviceAttempt(device_hash=hash_token(device_code), code_hash=hash_token(user_code),
        expires_at=now + timedelta(minutes=10), next_poll_at=now + timedelta(seconds=5), poll_seconds=5))
    db.commit()
    return {"device_code": device_code, "user_code": user_code,
        "verification_uri": f"https://{settings.public_host}/?mobile=1",
        "verification_uri_complete": f"https://{settings.public_host}/?mobile=1#code={user_code}",
        "expires_in": 600, "interval": 5}


def approve_device(user_code: str, user: User, db: Session) -> None:
    rate_limit(db, "approve:" + str(user.id), 20, 600)
    rate_limit(db, "approve", 100, 600)
    attempt = db.scalar(select(MobileDeviceAttempt).where(
        MobileDeviceAttempt.code_hash == hash_token(user_code)).with_for_update())
    if attempt is None or as_utc(attempt.expires_at) <= utcnow() or attempt.user_id is not None:
        raise MobileFailure(400, "invalid or expired code", headers=NO_STORE)
    attempt.user_id = user.id
    db.commit()


def exchange_device(device_code: str, db: Session) -> TokenResult:
    rate_limit(db, "poll", 300, 60)
    attempt = db.scalar(select(MobileDeviceAttempt).where(
        MobileDeviceAttempt.device_hash == hash_token(device_code)).with_for_update())
    now = utcnow()
    error = None
    if attempt is None or as_utc(attempt.expires_at) <= now:
        error = "expired_token"
    elif as_utc(attempt.next_poll_at) > now:
        attempt.poll_seconds = min(attempt.poll_seconds + 5, 60)
        attempt.next_poll_at = now + timedelta(seconds=attempt.poll_seconds)
        error = "slow_down"
    elif attempt.user_id is None:
        attempt.next_poll_at = now + timedelta(seconds=attempt.poll_seconds)
        error = "authorization_pending"
    if error:
        db.commit()
        return TokenResult({"error": error}, status=400)
    user = db.get(User, attempt.user_id)
    if user is None or not user.is_active or not user.google_sub or user.role not in {"ADMIN", "USER"}:
        db.delete(attempt)
        db.commit()
        return TokenResult({"error": "access_denied"}, status=400)
    value = new_token()
    db.delete(attempt)
    db.add(MobileSession(user_id=user.id, token_hash=hash_token(value), expires_at=now + timedelta(days=30)))
    db.commit()
    return TokenResult({"access_token": value, "token_type": "Bearer", "expires_in": 2592000})


def authenticate(authorization: str | None, db: Session) -> MobileIdentity:
    if not authorization or not re.fullmatch(r"Bearer [A-Za-z0-9_-]{43}", authorization):
        raise MobileFailure(401, headers=NO_STORE)
    session = db.scalar(select(MobileSession).where(MobileSession.token_hash == hash_token(authorization[7:])))
    if session is None or as_utc(session.expires_at) <= utcnow():
        raise MobileFailure(401, headers=NO_STORE)
    user = db.get(User, session.user_id)
    if user is None or not user.is_active or not user.google_sub or user.role not in {"ADMIN", "USER"}:
        raise MobileFailure(401, headers=NO_STORE)
    return MobileIdentity(user, session)


def logout(identity: MobileIdentity, db: Session) -> None:
    db.delete(identity.session)
    db.commit()
