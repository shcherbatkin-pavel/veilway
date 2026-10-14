"""Browser-approved, revocable sessions limited to the owner's VPN profiles."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import get_db
from .models import VpnProfile
from .pki import PkiClient
from .profile_download import download_response, get_pki_client
from .profiles import profile_response
from .schemas import ProfileResponse
from .security import AuthenticatedUser, require_csrf

router = APIRouter(prefix="/mobile")
from . import mobile_service
from .mobile_service import MobileIdentity, NO_STORE


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_code: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    grant_type: str = Field(default="urn:ietf:params:oauth:grant-type:device_code",
                            pattern=r"^urn:ietf:params:oauth:grant-type:device_code$")


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_code: str = Field(pattern=r"^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")


def invoke(operation, *args):
    try:
        return operation(*args)
    except mobile_service.MobileFailure as error:
        raise HTTPException(error.status, error.detail, headers=error.headers) from None


def rate_limit(db: Session, key: str, limit: int, seconds: int) -> None:
    """Compatibility boundary for callers that expect HTTPException."""
    invoke(mobile_service.rate_limit, db, key, limit, seconds)


@router.post("/auth/device")
def device(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    return JSONResponse(invoke(mobile_service.allocate_device, db, settings), headers=NO_STORE)


@router.post("/auth/approve", status_code=204)
def approve(payload: ApprovalRequest, auth: AuthenticatedUser = Depends(require_csrf), db: Session = Depends(get_db)):
    invoke(mobile_service.approve_device, payload.user_code, auth.user, db)
    return Response(status_code=204, headers=NO_STORE)


@router.post("/auth/token")
def token(payload: TokenRequest, db: Session = Depends(get_db)):
    result = invoke(mobile_service.exchange_device, payload.device_code, db)
    return JSONResponse(result.payload, status_code=result.status, headers=NO_STORE)


def authenticate_mobile(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> MobileIdentity:
    return invoke(mobile_service.authenticate, authorization, db)


@router.post("/auth/logout", status_code=204)
def logout(auth: MobileIdentity = Depends(authenticate_mobile), db: Session = Depends(get_db)):
    mobile_service.logout(auth, db)
    return Response(status_code=204, headers=NO_STORE)


@router.get("/profiles", response_model=list[ProfileResponse])
def profiles(response: Response, auth: MobileIdentity = Depends(authenticate_mobile), db: Session = Depends(get_db),
             limit: int = Query(default=100, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    response.headers.update(NO_STORE)
    return [profile_response(profile) for profile in db.scalars(select(VpnProfile).where(
        VpnProfile.owner_id == auth.user.id).order_by(VpnProfile.created_at.desc(), VpnProfile.id).limit(limit).offset(offset))]


@router.post("/profiles/{profile_id}/download", response_class=Response)
def download(profile_id: uuid.UUID, auth: MobileIdentity = Depends(authenticate_mobile), db: Session = Depends(get_db),
             client: PkiClient = Depends(get_pki_client)):
    return download_response(profile_id, auth.user, db, client, owner_only=True)
