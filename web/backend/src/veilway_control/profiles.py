"""Profile transactions and access rules; no private material is persisted here."""
from datetime import timedelta, timezone
import hashlib
import json
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .access import ensure_admin, visible_profiles
from .models import ProfileAuditEvent, ProfileJob, User, VpnProfile, as_utc, utcnow
from .schemas import ProfileCreateRequest, ProfileJobResponse, ProfileResponse


def audit(db, actor_id, action, object_id, result):
    db.add(ProfileAuditEvent(actor_id=actor_id, action=action, object_id=object_id, result=result))


def profile_response(profile: VpnProfile) -> ProfileResponse:
    state = profile.status
    if state == "active" and as_utc(profile.expires_at) <= utcnow():
        state = "expired"
    return ProfileResponse(id=profile.id, device_name=profile.device_name, mode=profile.mode,
        owner_id=profile.owner_id, status=state, created_at=as_utc(profile.created_at), expires_at=as_utc(profile.expires_at))


def job_response(job: ProfileJob) -> ProfileJobResponse:
    values = {name: getattr(job, name) for name in ProfileJobResponse.model_fields}
    for name in ("created_at", "started_at", "finished_at"):
        if values[name] is not None:
            values[name] = as_utc(values[name])
    return ProfileJobResponse(**values)


def locked_profile(db: Session, user: User, profile_id: uuid.UUID) -> VpnProfile:
    profile = db.scalar(visible_profiles(user).where(VpnProfile.id == profile_id).with_for_update())
    if profile is None:
        raise HTTPException(404)
    return profile


def registered_owner(db: Session, owner_id: uuid.UUID | None):
    if owner_id is None:
        return
    owner = db.scalar(select(User).where(User.id == owner_id, User.role == "USER", User.is_active.is_(True),
                                         User.google_sub.is_not(None)).with_for_update())
    if owner is None:
        raise HTTPException(422, "owner must be a registered active USER")


def request_digest(payload: ProfileCreateRequest) -> bytes:
    value = payload.model_dump(mode="json", exclude={"idempotency_key"})
    value["duration_days"] = payload.duration_days if payload.duration_days is not None else (None if payload.expires_at else 365)
    if payload.expires_at:
        value["expires_at"] = payload.expires_at.astimezone(timezone.utc).isoformat()
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).digest()


def replay(db, actor, key, digest, kind, profile_id=None):
    job = db.scalar(select(ProfileJob).where(ProfileJob.idempotency_key == key))
    if job is None:
        return None
    if (job.requested_by_id != actor.id or job.request_hash != digest or job.kind != kind
        or profile_id is not None and job.profile_id != profile_id):
        raise HTTPException(409, "idempotency key conflict")
    return job


def create_profile(db: Session, actor: User, payload: ProfileCreateRequest):
    ensure_admin(actor)
    if payload.duration_days is not None and payload.expires_at is not None:
        raise HTTPException(422, "specify duration or expiry, not both")
    digest = request_digest(payload)
    existing = replay(db, actor, payload.idempotency_key, digest, "issue")
    if existing:
        return db.get(VpnProfile, existing.profile_id), existing
    now = utcnow().replace(microsecond=0)
    try:
        expires = payload.expires_at.astimezone(timezone.utc) if payload.expires_at else now + timedelta(days=payload.duration_days or 365)
    except (OverflowError, ValueError):
        raise HTTPException(422, "expiry is outside the supported calendar") from None
    if expires <= now:
        raise HTTPException(422, "expiry must be in the future")
    registered_owner(db, payload.owner_id)
    profile = VpnProfile(device_name=payload.device_name, mode=payload.mode, owner_id=payload.owner_id,
        created_by_id=actor.id, created_at=now, expires_at=expires, status="issuing")
    try:
        db.add(profile)
        db.flush()
        job = ProfileJob(profile_id=profile.id, requested_by_id=actor.id, kind="issue", status="queued",
                         idempotency_key=payload.idempotency_key, request_hash=digest)
        db.add(job)
        audit(db, actor.id, "create", profile.id, "accepted")
        db.commit()
    except IntegrityError:
        db.rollback()
        job = replay(db, actor, payload.idempotency_key, digest, "issue")
        if job is None:
            raise HTTPException(409, "profile request conflict") from None
        profile = db.get(VpnProfile, job.profile_id)
    return profile, job


def rename_profile(db, actor, profile_id, name):
    ensure_admin(actor)
    profile = locked_profile(db, actor, profile_id)
    profile.device_name = name
    audit(db, actor.id, "rename", profile.id, "succeeded")
    db.commit()
    return profile


def assign_profile(db, actor, profile_id, owner_id):
    ensure_admin(actor)
    profile = locked_profile(db, actor, profile_id)
    if profile.owner_id is not None and profile.owner_id != owner_id:
        audit(db, actor.id, "assign", profile.id, "denied")
        db.commit()
        raise HTTPException(409, "owner cannot be changed; revoke and issue a new profile")
    registered_owner(db, owner_id)
    profile.owner_id = owner_id
    audit(db, actor.id, "assign", profile.id, "succeeded")
    db.commit()
    return profile


def revoke_profile(db, actor, profile_id, key):
    ensure_admin(actor)
    profile = locked_profile(db, actor, profile_id)
    digest = hashlib.sha256(f"revoke:{profile_id}".encode()).digest()
    existing = replay(db, actor, key, digest, "revoke", profile_id)
    if existing:
        return existing
    # Share the original durable revoke job even if the browser sends a new key.
    existing = db.scalar(select(ProfileJob).where(ProfileJob.profile_id == profile_id, ProfileJob.kind == "revoke"))
    if existing:
        return existing
    if profile.status not in {"active", "expired", "revoking"}:
        audit(db, actor.id, "revoke", profile.id, "denied")
        db.commit()
        raise HTTPException(409, "profile cannot be revoked in its current state")
    job = ProfileJob(profile_id=profile.id, requested_by_id=actor.id, idempotency_key=key,
                     request_hash=digest, kind="revoke", status="queued")
    try:
        profile.status = "revoking"  # Downloads stop as soon as the request commits.
        db.add(job)
        audit(db, actor.id, "revoke", profile.id, "accepted")
        db.commit()
    except IntegrityError:
        db.rollback()
        job = replay(db, actor, key, digest, "revoke", profile_id)
        if job is None:
            raise HTTPException(409, "profile request conflict") from None
    return job
