"""Browser-approved, revocable sessions limited to the owner's VPN profiles."""
from __future__ import annotations

import re
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import get_db
from .mobile_models import MobileDeviceAttempt, MobileRateBucket, MobileSession
from .models import User, VpnProfile, as_utc, utcnow
from .pki import PkiClient
from .profile_api import download_response, get_pki_client
from .profiles import profile_response
from .schemas import ProfileResponse
from .security import AuthenticatedUser, hash_token, new_token, require_csrf

router = APIRouter(prefix="/mobile")
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_code: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    grant_type: str = Field(default="urn:ietf:params:oauth:grant-type:device_code",
                            pattern=r"^urn:ietf:params:oauth:grant-type:device_code$")


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_code: str = Field(pattern=r"^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")


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
        raise HTTPException(429, "try again later", headers={**NO_STORE, "Retry-After": str(seconds)})


@router.post("/auth/device")
def device(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
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
    return JSONResponse({"device_code": device_code, "user_code": user_code,
        "verification_uri": f"https://{settings.public_host}/?mobile=1",
        "verification_uri_complete": f"https://{settings.public_host}/?mobile=1#code={user_code}",
        "expires_in": 600, "interval": 5}, headers=NO_STORE)


@router.post("/auth/approve", status_code=204)
def approve(payload: ApprovalRequest, auth: AuthenticatedUser = Depends(require_csrf), db: Session = Depends(get_db)):
    rate_limit(db, "approve:" + str(auth.user.id), 20, 600)
    rate_limit(db, "approve", 100, 600)
    attempt = db.scalar(select(MobileDeviceAttempt).where(
        MobileDeviceAttempt.code_hash == hash_token(payload.user_code)).with_for_update())
    if attempt is None or as_utc(attempt.expires_at) <= utcnow() or attempt.user_id is not None:
        raise HTTPException(400, "invalid or expired code", headers=NO_STORE)
    attempt.user_id = auth.user.id
    db.commit()
    return Response(status_code=204, headers=NO_STORE)


@router.post("/auth/token")
def token(payload: TokenRequest, db: Session = Depends(get_db)):
    rate_limit(db, "poll", 300, 60)
    attempt = db.scalar(select(MobileDeviceAttempt).where(
        MobileDeviceAttempt.device_hash == hash_token(payload.device_code)).with_for_update())
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
        return JSONResponse({"error": error}, status_code=400, headers=NO_STORE)
    user = db.get(User, attempt.user_id)
    if user is None or not user.is_active or not user.google_sub or user.role not in {"ADMIN", "USER"}:
        db.delete(attempt)
        db.commit()
        return JSONResponse({"error": "access_denied"}, status_code=400, headers=NO_STORE)
    value = new_token()
    db.delete(attempt)
    db.add(MobileSession(user_id=user.id, token_hash=hash_token(value), expires_at=now + timedelta(days=30)))
    db.commit()
    return JSONResponse({"access_token": value, "token_type": "Bearer", "expires_in": 2592000}, headers=NO_STORE)


def authenticate_mobile(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    if not authorization or not re.fullmatch(r"Bearer [A-Za-z0-9_-]{43}", authorization):
        raise HTTPException(401, headers=NO_STORE)
    session = db.scalar(select(MobileSession).where(MobileSession.token_hash == hash_token(authorization[7:])))
    if session is None or as_utc(session.expires_at) <= utcnow():
        raise HTTPException(401, headers=NO_STORE)
    user = db.get(User, session.user_id)
    if user is None or not user.is_active or not user.google_sub or user.role not in {"ADMIN", "USER"}:
        raise HTTPException(401, headers=NO_STORE)
    return user, session


@router.post("/auth/logout", status_code=204)
def logout(auth=Depends(authenticate_mobile), db: Session = Depends(get_db)):
    db.delete(auth[1])
    db.commit()
    return Response(status_code=204, headers=NO_STORE)


@router.get("/profiles", response_model=list[ProfileResponse])
def profiles(response: Response, auth=Depends(authenticate_mobile), db: Session = Depends(get_db),
             limit: int = Query(default=100, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    response.headers.update(NO_STORE)
    return [profile_response(profile) for profile in db.scalars(select(VpnProfile).where(
        VpnProfile.owner_id == auth[0].id).order_by(VpnProfile.created_at.desc(), VpnProfile.id).limit(limit).offset(offset))]


@router.post("/profiles/{profile_id}/download", response_class=Response)
def download(profile_id: uuid.UUID, auth=Depends(authenticate_mobile), db: Session = Depends(get_db),
             client: PkiClient = Depends(get_pki_client)):
    return download_response(profile_id, auth[0], db, client, owner_only=True)
