from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import pytest

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from veilway_control.cloud import CloudMutationError, CloudOperationError, RebootReceipt
from veilway_control.config import Settings
from veilway_control.models import (
    RestartJob,
    RestartTarget,
    VmHeartbeat,
    VpnVm,
    utcnow,
)
from veilway_control.orchestrator import RestartWorker


@dataclass
class FakeProvider:
    slug: str
    calls: list[str] = field(default_factory=list)
    operation_done: bool = True
    operation_error: CloudOperationError | None = None

    def reboot(self, vm: VpnVm) -> RebootReceipt:
        self.calls.append(vm.slug)
        return RebootReceipt(request_id=f"request-{self.slug}")

    def operation_complete(self, request_id: str) -> bool:
        assert request_id == f"request-{self.slug}"
        if self.operation_error is not None:
            raise self.operation_error
        return self.operation_done


def create_job(db_factory, ids, slugs=("aws-direct", "yc-direct")) -> None:
    with db_factory() as db:
        job = RestartJob(user_id=ids["user_id"], status="queued", active_guard=1)
        db.add(job)
        db.flush()
        for position, slug in enumerate(slugs, start=1):
            vm = db.scalar(select(VpnVm).where(VpnVm.slug == slug))
            assert vm is not None and vm.heartbeat is not None
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


def update_heartbeat(db_factory, slug: str, boot_suffix: int, healthy: bool) -> None:
    with db_factory() as db:
        heartbeat = db.scalar(
            select(VmHeartbeat).join(VpnVm).where(VpnVm.slug == slug)
        )
        assert heartbeat is not None
        heartbeat.boot_id = f"00000000-0000-4000-8000-{boot_suffix:012d}"
        heartbeat.healthy = healthy
        heartbeat.received_at = utcnow() + timedelta(seconds=1)
        db.commit()


def load_job(db_factory) -> RestartJob:
    with db_factory() as db:
        job = db.scalar(
            select(RestartJob).options(selectinload(RestartJob.targets))
        )
        assert job is not None
        db.expunge(job)
        return job


def make_worker(db_factory):
    providers = {
        slug: FakeProvider(slug) for slug in ("aws-direct", "yc-direct")
    }
    worker = RestartWorker(
        Settings(public_host="testserver", restart_timeout_seconds=60),
        db_factory,
        provider_factory=lambda _settings, vm: providers[vm.slug],
    )
    return worker, providers


def test_two_targets_restart_strictly_aws_then_yandex(
    db_factory, seed_control_data
) -> None:
    ids = seed_control_data()
    create_job(db_factory, ids)
    worker, providers = make_worker(db_factory)

    worker.step()
    assert providers["aws-direct"].calls == ["aws-direct"]
    assert providers["yc-direct"].calls == []

    worker.step()
    assert providers["aws-direct"].calls == ["aws-direct"]
    update_heartbeat(db_factory, "aws-direct", 101, healthy=False)
    worker.step()
    assert providers["yc-direct"].calls == []

    update_heartbeat(db_factory, "aws-direct", 101, healthy=True)
    worker.step()
    worker.step()
    assert providers["yc-direct"].calls == ["yc-direct"]

    update_heartbeat(db_factory, "yc-direct", 202, healthy=True)
    worker.step()
    job = load_job(db_factory)
    assert job.status == "succeeded"
    assert job.active_guard is None
    assert [target.status for target in job.targets] == ["recovered", "recovered"]


def test_unchanged_boot_id_never_causes_a_second_cloud_call(
    db_factory, seed_control_data
) -> None:
    ids = seed_control_data()
    create_job(db_factory, ids, slugs=("aws-direct",))
    worker, providers = make_worker(db_factory)

    worker.step()
    worker.step()
    worker.step()
    assert providers["aws-direct"].calls == ["aws-direct"]
    assert load_job(db_factory).status == "waiting"


def test_interrupted_dispatch_is_marked_for_review_without_retry(
    db_factory, seed_control_data
) -> None:
    ids = seed_control_data()
    create_job(db_factory, ids, slugs=("aws-direct",))
    with db_factory() as db:
        job = db.scalar(select(RestartJob))
        target = db.scalar(select(RestartTarget))
        assert job is not None and target is not None
        job.status = "dispatching"
        target.status = "dispatching"
        target.dispatched_at = utcnow()
        db.commit()

    worker, providers = make_worker(db_factory)
    worker.recover_interrupted_dispatches()

    job = load_job(db_factory)
    assert job.status == "needs_review"
    assert job.active_guard is None
    assert job.targets[0].status == "needs_review"
    assert providers["aws-direct"].calls == []


def test_waiting_target_times_out(db_factory, seed_control_data) -> None:
    ids = seed_control_data()
    create_job(db_factory, ids, slugs=("aws-direct",))
    with db_factory() as db:
        job = db.scalar(select(RestartJob))
        target = db.scalar(select(RestartTarget))
        assert job is not None and target is not None
        job.status = "waiting"
        target.status = "waiting"
        target.cloud_request_id = "request-aws-direct"
        target.dispatched_at = utcnow() - timedelta(seconds=61)
        db.commit()

    worker, _ = make_worker(db_factory)
    worker.step()
    job = load_job(db_factory)
    assert job.status == "failed"
    assert job.error_code == "heartbeat_timeout"


def test_definitive_cloud_operation_failure_stops_job(
    db_factory, seed_control_data
) -> None:
    ids = seed_control_data()
    create_job(db_factory, ids, slugs=("yc-direct",))
    worker, providers = make_worker(db_factory)
    providers["yc-direct"].operation_error = CloudOperationError(
        "yandex_operation_failed", definitive=True
    )

    worker.step()
    worker.step()

    job = load_job(db_factory)
    assert job.status == "failed"
    assert job.error_code == "yandex_operation_failed"
    assert providers["yc-direct"].calls == ["yc-direct"]


@pytest.mark.parametrize("ambiguous,status", [(True, "needs_review"), (False, "failed")])
def test_mutation_failure_never_retries(db_factory, seed_control_data, ambiguous, status):
    ids = seed_control_data()
    create_job(db_factory, ids)
    worker, providers = make_worker(db_factory)

    def fail_reboot(vm):
        # The dispatch marker must already be durable before any cloud mutation.
        job = load_job(db_factory)
        assert job.status == "dispatching"
        assert job.targets[0].status == "dispatching"
        providers[vm.slug].calls.append(vm.slug)
        raise CloudMutationError("synthetic_mutation_error", ambiguous=ambiguous)

    providers["aws-direct"].reboot = fail_reboot
    worker.step()
    worker.step()
    worker.recover_interrupted_dispatches()
    worker.step()
    job = load_job(db_factory)
    assert job.status == status
    assert job.targets[0].status == status
    assert job.error_code == "synthetic_mutation_error"
    assert job.active_guard is None
    assert providers["aws-direct"].calls == ["aws-direct"]
    assert providers["yc-direct"].calls == []
