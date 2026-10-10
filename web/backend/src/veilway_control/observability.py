"""Administrator-only current health, derived from existing operations."""
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .crl_api import delivery_snapshot
from .database import get_db
from .models import (ALLOWED_VM_SLUGS, CrlSyncState, ProfileJob, RestartJob,
                     RestartTarget, VpnProfile, as_utc, utcnow)
from .security import require_admin
from .services import list_vm_states
from .workers import StepSnapshot

Assessment = Literal["ok", "attention", "unknown"]
router = APIRouter()


class NodeHealth(BaseModel):
    slug: Literal["aws-direct", "yc-direct"]
    state: Literal["healthy", "degraded", "unknown", "restarting"]
    assessment: Assessment
    reasons: list[str]
    last_heartbeat_at: datetime | None
    heartbeat_age_seconds: int | None
    uptime_seconds: int | None
    containers: dict[str, Literal["healthy", "starting", "unhealthy", "missing"]] | None


class WorkerHealth(BaseModel):
    name: Literal["restart-worker", "profile-worker", "crl-worker"]
    assessment: Assessment
    state: Literal["starting", "running", "stopped", "stopping"]
    started_at: datetime | None
    completed_at: datetime | None
    succeeded_at: datetime | None
    in_progress: bool
    error_code: str | None
    slow: bool


class CrlNodeHealth(BaseModel):
    slug: Literal["aws-direct", "yc-direct"]
    status: str
    acknowledged_version: int | None
    last_contact_at: datetime | None
    acknowledged_at: datetime | None
    error_code: str | None


class CrlHealth(BaseModel):
    assessment: Assessment
    version: int | None
    next_update: datetime | None
    publisher_error: str | None
    attempted_at: datetime | None
    observation_stale: bool
    publication_expired: bool
    nodes: list[CrlNodeHealth]


class OperationHealth(BaseModel):
    kind: Literal["issue", "revoke", "restart"]
    assessment: Assessment
    queued: int
    running: int
    oldest_pending_age_seconds: int | None
    delayed: int
    needs_review: int
    failed_last_day: int
    pki_errors: int
    awaiting_delivery: int = 0


class Overview(BaseModel):
    generated_at: datetime
    assessment: Assessment
    nodes: list[NodeHealth]
    workers: list[WorkerHealth]
    crl: CrlHealth
    operations: list[OperationHealth]


def age(now, value):
    return max(0, int((now - as_utc(value)).total_seconds())) if value else None


def assessment(values):
    return "attention" if "attention" in values else "unknown" if "unknown" in values else "ok"


def count(db, model, *conditions):
    return db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


def operation_health(db, now, settings):
    result = []
    cutoff = now - timedelta(minutes=5)
    for kind, model in (("issue", ProfileJob), ("revoke", ProfileJob), ("restart", RestartJob)):
        base = [ProfileJob.kind == kind] if model is ProfileJob else []
        pending = [*base, model.status.in_(["queued", "running"] if model is ProfileJob
                                          else ["queued", "dispatching", "waiting"])]
        oldest = db.scalar(select(func.min(model.created_at)).where(*pending))
        queued = count(db, model, *base, model.status == "queued")
        running = count(db, model, *pending, model.status != "queued")
        review = count(db, model, *base, model.status == "needs_review")
        failed = count(db, model, *base, model.status == "failed",
                       model.finished_at >= now - timedelta(days=1))
        delayed = count(db, model, *base, model.status == "queued", model.created_at < cutoff)
        pki_errors = 0
        awaiting = 0
        if model is ProfileJob:
            delayed += count(db, model, *base, model.status == "running", model.lease_until < now)
            pki_errors = count(db, model, *pending, model.error_code.in_([
                "pki_unavailable", "pki_operation_failed", "pki_storage_error"]))
            if kind == "revoke":
                waiting = [ProfileJob.kind == "revoke", ProfileJob.status == "succeeded",
                           VpnProfile.status == "revoking", ProfileJob.crl_number.is_not(None)]
                awaiting = db.scalar(select(func.count()).select_from(ProfileJob).join(VpnProfile).where(*waiting)) or 0
                delayed += db.scalar(select(func.count()).select_from(ProfileJob).join(VpnProfile).where(
                    *waiting, ProfileJob.finished_at < cutoff)) or 0
        else:
            delayed += db.scalar(select(func.count(func.distinct(RestartTarget.job_id)))
                .join(RestartJob).where(RestartJob.active_guard == 1,
                    RestartTarget.status == "waiting",
                    RestartTarget.dispatched_at < now - timedelta(seconds=settings.restart_timeout_seconds))) or 0
        result.append(OperationHealth(kind=kind, assessment="attention" if delayed or review or failed or pki_errors else "ok",
            queued=queued, running=running, oldest_pending_age_seconds=age(now, oldest),
            delayed=delayed, needs_review=review, failed_last_day=failed, pki_errors=pki_errors,
            awaiting_delivery=awaiting))
    return result


def build_overview(db, settings, lifecycle, now):
    nodes = []
    for vm, state in list_vm_states(db, settings, now=now):
        heartbeat = vm.heartbeat
        reasons = []
        if heartbeat is None:
            reasons.append("heartbeat_missing")
        else:
            if as_utc(heartbeat.received_at) < now - timedelta(seconds=settings.heartbeat_stale_seconds):
                reasons.append("heartbeat_stale")
            if not heartbeat.healthy:
                reasons.append("component_unhealthy")
            if heartbeat.containers is None:
                reasons.append("details_missing")
        status = "attention" if any(r in reasons for r in ("heartbeat_stale", "component_unhealthy")) else "unknown" if reasons else "ok"
        if state == "restarting":
            status, reasons = "ok", ["planned_restart"]
        nodes.append(NodeHealth(slug=vm.slug, state=state, assessment=status, reasons=reasons,
            last_heartbeat_at=as_utc(heartbeat.received_at) if heartbeat else None,
            heartbeat_age_seconds=age(now, heartbeat.received_at) if heartbeat else None,
            uptime_seconds=heartbeat.uptime_seconds if heartbeat else None,
            containers=heartbeat.containers if heartbeat else None))
    # Even an unregistered expected node must be visible as missing.
    for slug in ALLOWED_VM_SLUGS:
        if not any(node.slug == slug for node in nodes):
            nodes.append(NodeHealth(slug=slug, state="unknown", assessment="unknown", reasons=["node_unconfigured"],
                last_heartbeat_at=None, heartbeat_age_seconds=None, uptime_seconds=None, containers=None))
    workers = []
    for name in ("restart-worker", "profile-worker", "crl-worker"):
        state = lifecycle.states.get(name) if lifecycle else None
        snapshot = getattr(getattr(state, "worker", None), "observation", None)
        step = snapshot.snapshot if snapshot else StepSnapshot()
        error = state.error_code if state and state.error_code else step.error_code
        running = bool(state and state.started and state.task is not None and not state.task.done())
        stopping = bool(lifecycle and lifecycle.stopping)
        slow = bool(step.in_progress and age(now, step.started_at) > 180)
        stale = step.completed_at is None or (not step.in_progress and
            age(now, step.completed_at) > max(30, settings.worker_interval_seconds * 3))
        workers.append(WorkerHealth(name=name,
            assessment="attention" if error or slow else "unknown" if stale or not running or stopping else "ok",
            state="stopping" if stopping else "running" if running else "stopped" if state and state.started else "starting",
            started_at=step.started_at, completed_at=step.completed_at, succeeded_at=step.succeeded_at,
            in_progress=step.in_progress, error_code=error, slow=slow))
    delivery = delivery_snapshot(db, now)
    sync = db.get(CrlSyncState, 1)
    attempted = as_utc(sync.attempted_at) if sync and sync.attempted_at else None
    stale = attempted is None or age(now, attempted) > 180
    expired = bool(delivery["next_update"] and as_utc(delivery["next_update"]) <= now)
    crl_status = "attention" if (delivery["publisher_error"] or expired or any(
        n["status"] in {"error", "offline", "expired", "pending"} for n in delivery["nodes"])) else "unknown" if (
        stale or delivery["version"] is None or any(n["status"] == "unconfigured" for n in delivery["nodes"])) else "ok"
    crl = CrlHealth(**delivery, assessment=crl_status, attempted_at=attempted,
                    observation_stale=stale, publication_expired=expired)
    operations = operation_health(db, now, settings)
    return Overview(generated_at=now, assessment=assessment([
        *(n.assessment for n in nodes), *(w.assessment for w in workers), crl.assessment,
        *(o.assessment for o in operations)]), nodes=nodes, workers=workers, crl=crl, operations=operations)


@router.get("/observability/overview", response_model=Overview)
def overview(request: Request, _=Depends(require_admin), db: Session = Depends(get_db),
             settings: Settings = Depends(get_settings)):
    return build_overview(db, settings, getattr(request.app.state, "worker_lifecycle", None), utcnow())
