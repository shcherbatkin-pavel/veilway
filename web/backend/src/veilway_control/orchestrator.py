from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .cloud import (
    CloudMutationError,
    CloudOperationError,
    CloudProvider,
    provider_for,
)
from .config import Settings
from .models import RestartJob, RestartTarget, VpnVm, as_utc, utcnow


ProviderFactory = Callable[[Settings, VpnVm], CloudProvider]


class RestartWorker:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        provider_factory: ProviderFactory = provider_for,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.provider_factory = provider_factory
        self._providers: dict[str, CloudProvider] = {}
        self._stopping = asyncio.Event()

    def _provider(self, vm: VpnVm) -> CloudProvider:
        provider = self._providers.get(vm.slug)
        if provider is None:
            provider = self.provider_factory(self.settings, vm)
            self._providers[vm.slug] = provider
        return provider

    def recover_interrupted_dispatches(self) -> None:
        with self.session_factory() as db:
            jobs = db.scalars(
                select(RestartJob).where(
                    RestartJob.active_guard == 1,
                    RestartJob.status == "dispatching",
                )
            ).all()
            for job in jobs:
                job.status = "needs_review"
                job.error_code = "backend_interrupted_dispatch"
                job.finished_at = utcnow()
                job.active_guard = None
                for target in job.targets:
                    if target.status == "dispatching":
                        target.status = "needs_review"
                        target.error_code = "dispatch_result_unknown"
            db.commit()

    async def run(self) -> None:
        await asyncio.to_thread(self.recover_interrupted_dispatches)
        while not self._stopping.is_set():
            await asyncio.to_thread(self.step)
            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self.settings.worker_interval_seconds,
                )
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stopping.set()

    def step(self) -> None:
        with self.session_factory() as db:
            job = db.scalar(
                select(RestartJob)
                .where(RestartJob.active_guard == 1)
                .options(
                    selectinload(RestartJob.targets).selectinload(RestartTarget.vm)
                )
                .order_by(RestartJob.created_at)
                .limit(1)
            )
            if job is None:
                return
            target = next(
                (candidate for candidate in job.targets if candidate.status != "recovered"),
                None,
            )
            if target is None:
                self._complete_job(db, job)
                return
            if target.status == "queued":
                self._dispatch(db, job, target)
                return
            if target.status == "waiting":
                self._wait_for_recovery(db, job, target)

    def _dispatch(
        self, db: Session, job: RestartJob, target: RestartTarget
    ) -> None:
        now = utcnow()
        job.status = "dispatching"
        job.started_at = job.started_at or now
        target.status = "dispatching"
        target.dispatched_at = now
        db.commit()

        try:
            receipt = self._provider(target.vm).reboot(target.vm)
        except CloudMutationError as error:
            db.refresh(job)
            db.refresh(target)
            target.status = "needs_review" if error.ambiguous else "failed"
            target.error_code = error.code
            job.status = target.status
            job.error_code = error.code
            job.finished_at = utcnow()
            job.active_guard = None
            db.commit()
            return

        db.refresh(job)
        db.refresh(target)
        target.cloud_request_id = receipt.request_id
        target.status = "waiting"
        job.status = "waiting"
        db.commit()

    def _wait_for_recovery(
        self, db: Session, job: RestartJob, target: RestartTarget
    ) -> None:
        if target.dispatched_at is None or target.cloud_request_id is None:
            self._fail_job(db, job, target, "invalid_persisted_state")
            return
        deadline = as_utc(target.dispatched_at) + timedelta(
            seconds=self.settings.restart_timeout_seconds
        )
        if utcnow() >= deadline:
            self._fail_job(db, job, target, "heartbeat_timeout")
            return
        try:
            operation_done = self._provider(target.vm).operation_complete(
                target.cloud_request_id
            )
        except CloudOperationError as error:
            if error.definitive:
                self._fail_job(db, job, target, error.code)
            return
        if not operation_done:
            return
        heartbeat = target.vm.heartbeat
        if (
            heartbeat is None
            or as_utc(heartbeat.received_at) <= as_utc(target.dispatched_at)
            or heartbeat.boot_id == target.previous_boot_id
            or not heartbeat.healthy
        ):
            return
        target.status = "recovered"
        target.recovered_at = utcnow()
        if all(candidate.status == "recovered" for candidate in job.targets):
            self._complete_job(db, job)
        else:
            job.status = "queued"
            db.commit()

    @staticmethod
    def _complete_job(db: Session, job: RestartJob) -> None:
        job.status = "succeeded"
        job.finished_at = utcnow()
        job.active_guard = None
        db.commit()

    @staticmethod
    def _fail_job(
        db: Session,
        job: RestartJob,
        target: RestartTarget,
        error_code: str,
    ) -> None:
        target.status = "failed"
        target.error_code = error_code
        job.status = "failed"
        job.error_code = error_code
        job.finished_at = utcnow()
        job.active_guard = None
        db.commit()
