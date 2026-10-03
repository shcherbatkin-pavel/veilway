from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .config import Settings
from .models import ALLOWED_VM_SLUGS, RestartJob, RestartTarget, VmHeartbeat, VpnVm, as_utc


VmState = Literal["healthy", "degraded", "unknown", "restarting"]


class RestartRejected(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def heartbeat_is_fresh_healthy(heartbeat: VmHeartbeat | None, stale_before: datetime) -> bool:
    return (
        heartbeat is not None
        and heartbeat.healthy
        and as_utc(heartbeat.received_at) >= stale_before
    )


def vm_state(vm: VpnVm, active_slugs: set[str], stale_before: datetime) -> VmState:
    if vm.slug in active_slugs:
        return "restarting"
    if vm.heartbeat is None:
        return "unknown"
    return "healthy" if heartbeat_is_fresh_healthy(vm.heartbeat, stale_before) else "degraded"


def list_vm_states(db: Session, settings: Settings, *, now: datetime) -> list[tuple[VpnVm, VmState]]:
    vms = db.scalars(
        select(VpnVm)
        .where(VpnVm.slug.in_(ALLOWED_VM_SLUGS))
        .options(selectinload(VpnVm.heartbeat))
        .order_by(VpnVm.restart_order)
    ).all()
    active_slugs = set(
        db.scalars(
            select(VpnVm.slug)
            .join(RestartTarget, RestartTarget.vm_id == VpnVm.id)
            .join(RestartJob, RestartJob.id == RestartTarget.job_id)
            .where(RestartJob.active_guard == 1)
        ).all()
    )
    stale_before = now - timedelta(seconds=settings.heartbeat_stale_seconds)
    return [(vm, vm_state(vm, active_slugs, stale_before)) for vm in vms]


def create_restart(
    db: Session, settings: Settings, admin_id: UUID, targets: list[str], *, now: datetime
) -> RestartJob:
    vms = db.scalars(
        select(VpnVm)
        .where(VpnVm.slug.in_(targets))
        .options(selectinload(VpnVm.heartbeat))
        .order_by(VpnVm.restart_order)
    ).all()
    if len(vms) != len(targets):
        raise RestartRejected(status_code=400, detail="unknown restart target")
    if any(vm.heartbeat is None for vm in vms):
        raise RestartRejected(status_code=409, detail="target has no heartbeat baseline")
    stale_before = now - timedelta(seconds=settings.heartbeat_stale_seconds)
    if any(not heartbeat_is_fresh_healthy(vm.heartbeat, stale_before) for vm in vms):
        raise RestartRejected(
            status_code=409,
            detail="target heartbeat is not fresh and healthy",
        )

    job = RestartJob(
        admin_id=admin_id,
        status="queued",
        active_guard=1,
    )
    try:
        db.add(job)
        db.flush()
        for position, vm in enumerate(vms, start=1):
            assert vm.heartbeat is not None
            db.add(
                RestartTarget(
                    job_id=job.id,
                    vm_id=vm.id,
                    position=position,
                    status="queued",
                    previous_boot_id=vm.heartbeat.boot_id,
                )
            )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise RestartRejected(
            status_code=409, detail="another restart job is active"
        ) from error
    persisted = db.scalar(
        select(RestartJob)
        .where(RestartJob.id == job.id)
        .options(selectinload(RestartJob.targets).selectinload(RestartTarget.vm))
    )
    assert persisted is not None
    return persisted
