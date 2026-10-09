from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import registered_users, visible_jobs, visible_profiles
from .config import Settings, get_settings
from .database import get_db
from .models import ProfileAuditEvent, ProfileJob, User, VpnProfile, as_utc, utcnow
from .pki import PkiClient, PkiUnavailable
from .profiles import (assign_profile, audit, create_profile, job_response, locked_profile,
                       profile_response, rename_profile, revoke_profile)
from .schemas import (ProfileAssignRequest, ProfileAuditResponse, ProfileCreateRequest, ProfileCreateResponse,
                      ProfileJobResponse, ProfileRenameRequest, ProfileResponse, ProfileRevokeRequest, RegisteredUserResponse)
from .security import AuthenticatedUser, authenticate_user, require_admin, require_admin_csrf, require_csrf

router = APIRouter()


def get_pki_client(settings: Settings = Depends(get_settings)):
    return PkiClient(settings.pki_socket_path)


@router.get("/users", response_model=list[RegisteredUserResponse])
def users(_: AuthenticatedUser = Depends(require_admin), db: Session = Depends(get_db),
          limit: int = Query(default=100, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return [RegisteredUserResponse(id=user.id, email=user.email) for user in db.scalars(
        registered_users().order_by(User.email, User.id).offset(offset).limit(limit))]


@router.get("/profiles", response_model=list[ProfileResponse])
def profiles(auth: AuthenticatedUser = Depends(authenticate_user), db: Session = Depends(get_db),
             limit: int = Query(default=100, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return [profile_response(profile) for profile in db.scalars(visible_profiles(auth.user).order_by(
        VpnProfile.created_at.desc(), VpnProfile.id).offset(offset).limit(limit))]


@router.post("/profiles", response_model=ProfileCreateResponse, status_code=202)
def create(payload: ProfileCreateRequest, auth: AuthenticatedUser = Depends(require_admin_csrf), db: Session = Depends(get_db)):
    profile, job = create_profile(db, auth.user, payload)
    return ProfileCreateResponse(profile=profile_response(profile), job=job_response(job))


@router.get("/profiles/{profile_id}", response_model=ProfileResponse)
def detail(profile_id: uuid.UUID, auth: AuthenticatedUser = Depends(authenticate_user), db: Session = Depends(get_db)):
    return profile_response(locked_profile(db, auth.user, profile_id))


@router.patch("/profiles/{profile_id}", response_model=ProfileResponse)
def rename(profile_id: uuid.UUID, payload: ProfileRenameRequest,
           auth: AuthenticatedUser = Depends(require_admin_csrf), db: Session = Depends(get_db)):
    return profile_response(rename_profile(db, auth.user, profile_id, payload.device_name))


@router.post("/profiles/{profile_id}/owner", response_model=ProfileResponse)
def assign(profile_id: uuid.UUID, payload: ProfileAssignRequest,
           auth: AuthenticatedUser = Depends(require_admin_csrf), db: Session = Depends(get_db)):
    return profile_response(assign_profile(db, auth.user, profile_id, payload.owner_id))


@router.post("/profiles/{profile_id}/revoke", response_model=ProfileJobResponse, status_code=202)
def revoke(profile_id: uuid.UUID, payload: ProfileRevokeRequest,
           auth: AuthenticatedUser = Depends(require_admin_csrf), db: Session = Depends(get_db)):
    return job_response(revoke_profile(db, auth.user, profile_id, payload.idempotency_key))


@router.post("/profiles/{profile_id}/download", response_class=Response)
def download(profile_id: uuid.UUID, auth: AuthenticatedUser = Depends(require_csrf), db: Session = Depends(get_db),
             client: PkiClient = Depends(get_pki_client)):
    profile = locked_profile(db, auth.user, profile_id)
    if profile.status != "active" or as_utc(profile.expires_at) <= utcnow():
        audit(db, auth.user.id, "download", profile.id, "denied")
        db.commit()
        raise HTTPException(409, "profile is not active", headers={"Cache-Control": "no-store"})
    try:
        material = client.download(profile.id)
    except PkiUnavailable:
        audit(db, auth.user.id, "download", profile.id, "unavailable")
        db.commit()
        raise HTTPException(503, "profile download unavailable", headers={"Cache-Control": "no-store"}) from None
    audit(db, auth.user.id, "download", profile.id, "succeeded")
    db.commit()
    return Response(material, media_type="application/x-openvpn-profile", headers={
        "Content-Disposition": f'attachment; filename="veilway-{profile.id}.ovpn"',
        "Cache-Control": "no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    })


@router.get("/profile-jobs", response_model=list[ProfileJobResponse])
def jobs(auth: AuthenticatedUser = Depends(authenticate_user), db: Session = Depends(get_db),
         limit: int = Query(default=100, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return [job_response(job) for job in db.scalars(visible_jobs(auth.user).order_by(
        ProfileJob.created_at.desc(), ProfileJob.id).offset(offset).limit(limit))]


@router.get("/profile-jobs/{job_id}", response_model=ProfileJobResponse)
def job_detail(job_id: uuid.UUID, auth: AuthenticatedUser = Depends(authenticate_user), db: Session = Depends(get_db)):
    job = db.scalar(visible_jobs(auth.user).where(ProfileJob.id == job_id))
    if job is None:
        raise HTTPException(404)
    return job_response(job)


@router.get("/profile-audit-events", response_model=list[ProfileAuditResponse])
def history(_: AuthenticatedUser = Depends(require_admin), db: Session = Depends(get_db),
            limit: int = Query(default=100, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return [ProfileAuditResponse(**{name: getattr(event, name) for name in ProfileAuditResponse.model_fields})
            for event in db.scalars(select(ProfileAuditEvent).order_by(ProfileAuditEvent.created_at.desc(), ProfileAuditEvent.id).offset(offset).limit(limit))]
