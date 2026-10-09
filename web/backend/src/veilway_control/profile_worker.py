"""Lease-based PKI worker: every retry uses the original durable job key."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import uuid
from typing import Literal, TypedDict, cast

from sqlalchemy import and_, or_, select, update

from .models import ProfileJob, VpnProfile, as_utc, utcnow
from .pki import PkiClient, PkiUnavailable
from .profiles import audit


class IssueResult(TypedDict):
    serial: str
    certificate_sha256: str


class RevokeResult(TypedDict):
    crl_number: int


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    token: uuid.UUID
    profile_id: uuid.UUID
    kind: Literal["issue", "revoke"]
    key: uuid.UUID
    mode: str
    expires: str


@dataclass(frozen=True)
class PkiOutcome:
    result: IssueResult | RevokeResult | None = None
    error: str | None = None


class ProfileWorker:
    def __init__(self, settings, session_factory, client=None, *, fault=None):
        self.settings = settings
        self.session_factory = session_factory
        self.client = client or PkiClient(settings.pki_socket_path)
        self.fault = fault or (lambda point: None)
        self.stopping = asyncio.Event()

    def stop(self):
        self.stopping.set()

    async def run(self):
        while not self.stopping.is_set():
            try:
                await asyncio.to_thread(self.step)
            except Exception:
                # DB outages must not kill the worker or disclose DB/PKI inputs.
                # Committed leases are reclaimed automatically after expiration.
                pass
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=self.settings.worker_interval_seconds)
            except TimeoutError:
                pass

    def step(self, *, now: datetime | None = None) -> bool:
        now = now or utcnow()
        claim = self._claim(now)
        if claim is None:
            return False
        outcome = self._call_pki(claim)
        self.fault("after_pki")
        return self._complete(claim, outcome, now)

    def _claim(self, now: datetime) -> ClaimedJob | None:
        token = uuid.uuid4()
        with self.session_factory() as db:
            eligible = and_(
                or_(ProfileJob.retry_at.is_(None), ProfileJob.retry_at <= now),
                or_(ProfileJob.status == "queued", and_(ProfileJob.status == "running", ProfileJob.lease_until <= now)),
            )
            job = db.scalar(select(ProfileJob).where(eligible).order_by(ProfileJob.created_at, ProfileJob.id)
                            .with_for_update(skip_locked=True).limit(1))
            if job is None:
                return None
            # CAS also protects claims on lightweight databases without row locks.
            claimed = db.execute(update(ProfileJob).where(ProfileJob.id == job.id, eligible).values(
                status="running", claim_token=token, lease_until=now + timedelta(seconds=180),
                attempts=ProfileJob.attempts + 1, started_at=job.started_at or now,
            ).execution_options(synchronize_session=False))
            if claimed.rowcount != 1:
                db.rollback()
                return None
            job_id = job.id
            db.commit()
            db.refresh(job)
            profile = db.get(VpnProfile, job.profile_id)
            return ClaimedJob(
                id=job_id, token=token, profile_id=profile.id,
                kind=cast(Literal["issue", "revoke"], job.kind), key=job.idempotency_key,
                mode=profile.mode, expires=as_utc(profile.expires_at).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )

    def _call_pki(self, claim: ClaimedJob) -> PkiOutcome:
        try:
            if claim.kind == "issue":
                result = self.client.issue(claim.profile_id, claim.key, claim.mode, claim.expires)
            else:
                result = self.client.revoke(claim.profile_id, claim.key)
            return PkiOutcome(result=cast(IssueResult | RevokeResult, result))
        except PkiUnavailable as rejected:
            return PkiOutcome(error=rejected.code)
        except Exception:
            return PkiOutcome(error="unavailable")

    def _complete(self, claim: ClaimedJob, outcome: PkiOutcome, now: datetime) -> bool:
        error = outcome.error
        kind = claim.kind
        with self.session_factory() as db:
            job = db.scalar(select(ProfileJob).where(ProfileJob.id == claim.id).with_for_update())
            if job is None or job.claim_token != claim.token or job.status != "running":
                return True  # A reclaimed lease owns completion now.
            profile = db.scalar(select(VpnProfile).where(VpnProfile.id == job.profile_id).with_for_update())
            if error is None:
                if kind == "issue":
                    result = cast(IssueResult, outcome.result)
                    profile.certificate_serial = result["serial"]
                    profile.certificate_sha256 = result["certificate_sha256"]
                    profile.status = "active" if as_utc(profile.expires_at) > now else "expired"
                    audit_result = "succeeded"
                else:
                    job.crl_number = cast(RevokeResult, outcome.result)["crl_number"]
                    # CA-local success does not prove that any node received the CRL.
                    profile.status = "revoking"
                    audit_result = "local_revocation_applied"
                job.status = "succeeded"
                job.finished_at = now
                job.error_code = None
                audit(db, job.requested_by_id, f"{kind}_result", profile.id, audit_result)
            elif error in {"unavailable", "operation_failed", "storage_error"}:
                job.status = "queued"
                job.retry_at = now + timedelta(seconds=min(300, 2 ** min(job.attempts, 8)))
                job.error_code = f"pki_{error}"
            else:
                definitive = kind == "issue" and error in {"expired", "invalid_request"}
                job.status = "failed" if definitive else "needs_review"
                job.error_code = "pki_expiry_rejected" if error == "expired" else "pki_request_rejected"
                job.finished_at = now
                if kind == "issue":
                    profile.status = "failed"
                audit(db, job.requested_by_id, f"{kind}_result", profile.id, job.status)
            job.claim_token = None
            job.lease_until = None
            if job.status != "queued":
                job.retry_at = None
            self.fault("before_db_commit")
            db.commit()
        return True
