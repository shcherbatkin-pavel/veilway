from __future__ import annotations

import hmac
import re
import uuid
from datetime import timedelta

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .services import RestartRejected, create_restart, list_vm_states
from .config import Settings, get_settings
from .database import get_db
from .models import (
    ALLOWED_VM_SLUGS,
    OAuthLoginAttempt,
    UserSession,
    RestartJob,
    RestartTarget,
    VmHeartbeat,
    VpnVm,
    utcnow,
)
from .oidc import GoogleOidcClient, OidcRejected, get_google_client, resolve_google_user
from .profile_api import router as profile_router
from .crl_api import router as crl_router
from .schemas import (
    HeartbeatRequest,
    RestartCreateRequest,
    RestartJobResponse,
    RestartTargetResponse,
    SessionResponse,
    VmResponse,
)
from .security import (
    AuthenticatedUser,
    authenticate_user,
    create_user_session,
    hash_token,
    new_token,
    require_csrf,
    require_admin,
    require_admin_csrf,
)


router = APIRouter(prefix="/api/v1")
# Preserve the existing router import used by application/test consumers.
router.include_router(profile_router, prefix="")
router.include_router(crl_router)


def auth_redirect(error: str | None = None) -> RedirectResponse:
    response = RedirectResponse("/" if error is None else f"/?auth_error={error}", status_code=303)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/auth/google/start")
def google_start(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    client: GoogleOidcClient = Depends(get_google_client),
    browser_cookie: str | None = Cookie(default=None, alias="__Host-veilway_oauth"),
) -> Response:
    try:
        # Reject incomplete configuration before creating an attempt.
        settings.admin_google_email
        settings.google_client_secret
        state, nonce, browser = new_token(), new_token(), new_token()
        url = client.authorization_url(settings, state, nonce)
    except (RuntimeError, ValueError):
        return auth_redirect("unavailable")
    now = utcnow()
    expired = OAuthLoginAttempt.expires_at <= now
    if browser_cookie:
        expired = expired | (OAuthLoginAttempt.browser_hash == hash_token(browser_cookie))
    db.execute(delete(OAuthLoginAttempt).where(expired))
    db.add(OAuthLoginAttempt(
        state_hash=hash_token(state), nonce_hash=hash_token(nonce), browser_hash=hash_token(browser),
        expires_at=now + timedelta(seconds=settings.oauth_attempt_seconds),
    ))
    db.commit()
    response = RedirectResponse(url, status_code=303, headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    })
    response.set_cookie(
        settings.oauth_cookie_name, browser, max_age=settings.oauth_attempt_seconds,
        secure=settings.cookie_secure, httponly=True, samesite="lax", path="/",
    )
    return response


@router.get("/auth/google/callback")
def google_callback(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    client: GoogleOidcClient = Depends(get_google_client),
    browser_cookie: str | None = Cookie(default=None, alias="__Host-veilway_oauth"),
) -> Response:
    response = auth_redirect("login_failed")
    response.delete_cookie(settings.oauth_cookie_name, secure=settings.cookie_secure,
                           httponly=True, samesite="lax", path="/")
    query = request.query_params
    if any(len(query.getlist(key)) > 1 for key in ("state", "code", "error")):
        return response
    state_value = query.get("state", "")
    if not browser_cookie or not re.fullmatch(r"[A-Za-z0-9_-]{43}", browser_cookie):
        return response
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", state_value):
        return response
    # Atomic deletion + RETURNING is one-use across concurrent callbacks.
    nonce_hash = db.scalar(delete(OAuthLoginAttempt).where(
        OAuthLoginAttempt.state_hash == hash_token(state_value),
        OAuthLoginAttempt.browser_hash == hash_token(browser_cookie),
        OAuthLoginAttempt.expires_at > utcnow(),
    ).returning(OAuthLoginAttempt.nonce_hash))
    db.commit()
    if nonce_hash is None:
        return response
    if "error" in query:
        response.headers["Location"] = "/?auth_error=cancelled"
        return response
    code = query.get("code", "")
    if not code or len(code) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in code):
        return response
    try:
        identity = client.exchange(settings, code, nonce_hash)
        user = resolve_google_user(db, identity, settings.admin_google_email)
        # Successful login rotates the browser's session instead of adopting it.
        _, token, _ = create_user_session(db, user, settings)
    except (OidcRejected, RuntimeError, HTTPException, IntegrityError):
        db.rollback()
        return response
    response.headers["Location"] = "/"
    response.set_cookie(
        settings.session_cookie_name, token, max_age=settings.session_hours * 3600,
        secure=settings.cookie_secure, httponly=True, samesite="strict", path="/",
    )
    return response


def serialize_job(job: RestartJob) -> RestartJobResponse:
    return RestartJobResponse(
        id=job.id,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_code=job.error_code,
        targets=[
            RestartTargetResponse(
                slug=target.vm.slug,
                position=target.position,
                status=target.status,
                dispatched_at=target.dispatched_at,
                recovered_at=target.recovered_at,
                error_code=target.error_code,
            )
            for target in job.targets
        ],
    )


@router.get("/auth/session", response_model=SessionResponse)
def current_session(
    response: Response,
    authenticated: AuthenticatedUser = Depends(authenticate_user),
    db: Session = Depends(get_db),
) -> SessionResponse:
    response.headers["Cache-Control"] = "no-store"
    csrf_token = new_token()
    authenticated.session.csrf_hash = hash_token(csrf_token)
    db.commit()
    return SessionResponse(
        user_id=authenticated.user.id,
        email=authenticated.user.email,
        role=authenticated.user.role,
        csrf_token=csrf_token,
    )


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def logout(
    response: Response,
    authenticated: AuthenticatedUser = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    session = db.get(UserSession, authenticated.session.id)
    if session is not None:
        db.delete(session)
        db.commit()
    response.delete_cookie(
        settings.session_cookie_name,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/vpn-vms", response_model=list[VmResponse])
def list_vpn_vms(
    _: AuthenticatedUser = Depends(require_admin),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[VmResponse]:
    return [
        VmResponse(
            slug=vm.slug,
            provider=vm.provider,
            state=state,
            last_heartbeat_at=vm.heartbeat.received_at if vm.heartbeat else None,
        )
        for vm, state in list_vm_states(db, settings, now=utcnow())
    ]


@router.post(
    "/restart-jobs",
    response_model=RestartJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_restart_job(
    payload: RestartCreateRequest,
    authenticated: AuthenticatedUser = Depends(require_admin_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RestartJobResponse:
    try:
        job = create_restart(db, settings, authenticated.user.id, payload.targets, now=utcnow())
    except RestartRejected as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    return serialize_job(job)

@router.get("/restart-jobs", response_model=list[RestartJobResponse])
def list_restart_jobs(
    _: AuthenticatedUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[RestartJobResponse]:
    jobs = db.scalars(
        select(RestartJob)
        .options(selectinload(RestartJob.targets).selectinload(RestartTarget.vm))
        .order_by(RestartJob.created_at.desc())
        .limit(25)
    ).all()
    return [serialize_job(job) for job in jobs]


@router.get("/restart-jobs/{job_id}", response_model=RestartJobResponse)
def get_restart_job(
    job_id: uuid.UUID,
    _: AuthenticatedUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> RestartJobResponse:
    job = db.scalar(
        select(RestartJob)
        .where(RestartJob.id == job_id)
        .options(selectinload(RestartJob.targets).selectinload(RestartTarget.vm))
    )
    if job is None:
        raise HTTPException(status_code=404)
    return serialize_job(job)


@router.post(
    "/agents/{vm_slug}/heartbeat",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def receive_heartbeat(
    vm_slug: str,
    payload: HeartbeatRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Response:
    if vm_slug not in ALLOWED_VM_SLUGS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    vm = db.scalar(select(VpnVm).where(VpnVm.slug == vm_slug))
    if vm is None or not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    supplied = hash_token(authorization.removeprefix("Bearer ").strip())
    if not hmac.compare_digest(vm.heartbeat_token_hash, supplied):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    heartbeat = db.scalar(select(VmHeartbeat).where(VmHeartbeat.vm_id == vm.id))
    if heartbeat is None:
        heartbeat = VmHeartbeat(vm_id=vm.id)
        db.add(heartbeat)
    heartbeat.boot_id = str(payload.boot_id)
    heartbeat.healthy = payload.healthy
    heartbeat.uptime_seconds = payload.uptime_seconds
    heartbeat.received_at = utcnow()
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
