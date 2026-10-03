from __future__ import annotations

import hmac
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .services import RestartRejected, create_restart, list_vm_states
from .config import Settings, get_settings
from .database import get_db
from .models import (
    ALLOWED_VM_SLUGS,
    Admin,
    AdminSession,
    RestartJob,
    VmHeartbeat,
    VpnVm,
    utcnow,
)
from .schemas import (
    HeartbeatRequest,
    LoginRequest,
    RestartCreateRequest,
    RestartJobResponse,
    RestartTargetResponse,
    SessionResponse,
    VmResponse,
)
from .security import (
    DUMMY_PASSWORD_HASH,
    AuthenticatedAdmin,
    authenticate_admin,
    create_admin_session,
    hash_token,
    new_token,
    require_csrf,
    verify_password,
)


router = APIRouter(prefix="/api/v1")


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


@router.post("/auth/login", response_model=SessionResponse)
def login(
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionResponse:
    admin = db.scalar(select(Admin).where(Admin.login == payload.login))
    password_hash = admin.password_hash if admin is not None else DUMMY_PASSWORD_HASH
    if not verify_password(password_hash, payload.password) or admin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )
    _, session_token, csrf_token = create_admin_session(db, admin, settings)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        max_age=settings.session_hours * 3600,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return SessionResponse(login=admin.login, csrf_token=csrf_token)


@router.get("/auth/session", response_model=SessionResponse)
def current_session(
    authenticated: AuthenticatedAdmin = Depends(authenticate_admin),
    db: Session = Depends(get_db),
) -> SessionResponse:
    csrf_token = new_token()
    authenticated.session.csrf_hash = hash_token(csrf_token)
    db.commit()
    return SessionResponse(login=authenticated.admin.login, csrf_token=csrf_token)


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def logout(
    response: Response,
    authenticated: AuthenticatedAdmin = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    session = db.get(AdminSession, authenticated.session.id)
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
    _: AuthenticatedAdmin = Depends(authenticate_admin),
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
    authenticated: AuthenticatedAdmin = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RestartJobResponse:
    try:
        job = create_restart(db, settings, authenticated.admin.id, payload.targets, now=utcnow())
    except RestartRejected as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    return serialize_job(job)

@router.get("/restart-jobs", response_model=list[RestartJobResponse])
def list_restart_jobs(
    _: AuthenticatedAdmin = Depends(authenticate_admin),
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
    _: AuthenticatedAdmin = Depends(authenticate_admin),
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
