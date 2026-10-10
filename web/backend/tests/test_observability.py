from datetime import timedelta
from types import SimpleNamespace
import asyncio

from test_api import build_client, login
from veilway_control.config import Settings
from veilway_control.models import (CrlAgent, CrlPublication, CrlSyncState, ProfileJob,
    RestartJob, RestartTarget, User, VmHeartbeat, VpnProfile, utcnow)
from veilway_control.observability import build_overview
from veilway_control.workers import StepObservation, StepSnapshot, run_periodic_step


def lifecycle(now):
    states = {}
    for name in ("restart-worker", "profile-worker", "crl-worker"):
        observation = StepObservation()
        observation.snapshot = StepSnapshot(now, now, now)
        states[name] = SimpleNamespace(worker=SimpleNamespace(observation=observation),
            started=True, task=SimpleNamespace(done=lambda: False), error_code=None)
    return SimpleNamespace(states=states, stopping=False)


def healthy(db, now):
    for heartbeat in db.query(VmHeartbeat):
        heartbeat.containers = {"veilway-openvpn": "healthy"}
        heartbeat.received_at = now
    db.get(CrlSyncState, 1).attempted_at = now
    db.add(CrlPublication(version=1, sha256="a" * 64, ca_sha256="b" * 64,
        pem=b"synthetic-no-certificate", this_update=now, next_update=now + timedelta(days=1)))
    for i, slug in enumerate(("aws-direct", "yc-direct")):
        db.add(CrlAgent(slug=slug, token_hash=bytes([i]) * 32, last_contact_at=now,
            acknowledged_version=1, acknowledged_until=now + timedelta(days=1), acknowledged_at=now))
    db.commit()


def test_auth_and_safe_snapshot(db_factory, seed_control_data):
    seed = seed_control_data()
    with build_client(db_factory) as client:
        url = "/api/v1/observability/overview"
        assert client.get(url).status_code == 401
        login(client)
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json()["nodes"][0]["reasons"] == ["details_missing"]
        for forbidden in ("instance_id", "region", "token_hash", "boot_id", "pem", "email", "password"):
            assert forbidden not in response.text
        with db_factory() as db:
            db.get(User, seed["user_id"]).role = "USER"
            db.commit()
        assert client.get(url).status_code == 403


def test_healthy_missing_stale_and_planned_restart(db_factory, seed_control_data):
    seed = seed_control_data()
    now = utcnow()
    workers = lifecycle(now)
    with db_factory() as db:
        healthy(db, now)
        assert build_overview(db, Settings(), workers, now).assessment == "ok"
        heartbeat = db.query(VmHeartbeat).filter_by(vm_id=seed["aws_id"]).one()
        heartbeat.received_at = now - timedelta(seconds=60)
        assert build_overview(db, Settings(), workers, now).nodes[0].assessment == "ok"
        heartbeat.received_at -= timedelta(microseconds=1)
        heartbeat.healthy = False
        heartbeat.containers = {"veilway-openvpn": "unhealthy"}
        result = build_overview(db, Settings(), workers, now)
        assert result.assessment == "attention"
        assert result.nodes[0].reasons == ["heartbeat_stale", "component_unhealthy"]
        job = RestartJob(user_id=seed["user_id"], status="waiting", active_guard=1)
        db.add(job); db.flush()
        db.add(RestartTarget(job_id=job.id, vm_id=seed["aws_id"], position=1,
            previous_boot_id="synthetic", status="waiting", dispatched_at=now))
        db.flush()
        node = build_overview(db, Settings(), workers, now).nodes[0]
        assert node.state == "restarting" and node.reasons == ["planned_restart"]
        db.delete(job); db.flush()
        db.delete(heartbeat); db.flush(); db.expire_all()
        node = build_overview(db, Settings(), workers, now).nodes[0]
        assert node.assessment == "unknown" and node.reasons == ["heartbeat_missing"]


def test_worker_observation_failure_recovery_and_no_private_text():
    async def scenario():
        stopping = asyncio.Event()
        observation = StepObservation()
        calls = []
        loop = asyncio.get_running_loop()
        def step():
            calls.append(observation.snapshot)
            if len(calls) == 1:
                raise RuntimeError("synthetic-private-material")
            loop.call_soon_threadsafe(stopping.set)
        await run_periodic_step(step, stopping, .001, continue_on_error=True, observation=observation)
        assert calls[0].in_progress
        assert calls[1].error_code == "step_failed"
        assert calls[1].completed_at and calls[1].succeeded_at is None
        assert observation.snapshot.error_code is None
        assert observation.snapshot.succeeded_at and not observation.snapshot.in_progress
        assert "synthetic-private-material" not in repr(observation.snapshot)
    asyncio.run(scenario())


def test_worker_and_crl_business_failure_are_separate(db_factory, seed_control_data):
    seed_control_data()
    now = utcnow()
    workers = lifecycle(now)
    with db_factory() as db:
        healthy(db, now)
        db.get(CrlSyncState, 1).error_code = "publication_unavailable"
        result = build_overview(db, Settings(), workers, now)
        assert result.workers[2].assessment == "ok"
        assert result.crl.assessment == "attention"
        workers.states["profile-worker"].worker.observation.snapshot = StepSnapshot(
            now - timedelta(seconds=181), now, now, True)
        assert build_overview(db, Settings(), workers, now).workers[1].slow
        workers.states["profile-worker"].error_code = "worker_failed"
        assert build_overview(db, Settings(), workers, now).workers[1].error_code == "worker_failed"
        workers.states["profile-worker"].error_code = None
        workers.states["profile-worker"].worker.observation.snapshot = StepSnapshot()
        assert build_overview(db, Settings(), workers, now).workers[1].assessment == "unknown"
        db.get(CrlSyncState, 1).error_code = None
        db.get(CrlSyncState, 1).attempted_at = now - timedelta(seconds=181)
        assert build_overview(db, Settings(), workers, now).crl.observation_stale
        publication = db.get(CrlPublication, 1)
        publication.next_update = now
        assert build_overview(db, Settings(), workers, now).crl.publication_expired
        agent = db.get(CrlAgent, "aws-direct")
        agent.last_contact_at = now - timedelta(seconds=121)
        assert build_overview(db, Settings(), workers, now).crl.nodes[0].status == "offline"


def test_operation_aggregates(db_factory, seed_control_data):
    seed = seed_control_data()
    now = utcnow()
    with db_factory() as db:
        healthy(db, now)
        for kind, status in (("issue", "queued"), ("issue", "running"), ("issue", "needs_review"),
                             ("issue", "failed"), ("revoke", "succeeded")):
            profile = VpnProfile(device_name="synthetic", mode="aws-direct", created_by_id=seed["user_id"],
                created_at=now - timedelta(minutes=10), expires_at=now + timedelta(days=1),
                status="revoking" if kind == "revoke" else "issuing")
            db.add(profile); db.flush()
            db.add(ProfileJob(profile_id=profile.id, requested_by_id=seed["user_id"], kind=kind,
                status=status, created_at=now - timedelta(minutes=6), finished_at=now - timedelta(minutes=6),
                lease_until=now - timedelta(seconds=1) if status == "running" else None,
                crl_number=1 if kind == "revoke" else None,
                error_code="pki_unavailable" if status == "queued" else None))
        db.flush()
        result = build_overview(db, Settings(), lifecycle(now), now)
        issue, revoke, _ = result.operations
        assert (issue.queued, issue.running, issue.delayed, issue.needs_review, issue.failed_last_day, issue.pki_errors) == (1, 1, 2, 1, 1, 1)
        assert issue.oldest_pending_age_seconds == 360
        assert revoke.awaiting_delivery == 1 and revoke.delayed == 1
        assert result.assessment == "attention"


def test_unconfigured_nodes_and_queue_threshold(db_factory, seed_control_data):
    now = utcnow()
    with db_factory() as db:
        result = build_overview(db, Settings(), None, now)
        assert len(result.nodes) == 2
        assert all(node.reasons == ["node_unconfigured"] for node in result.nodes)
        assert result.assessment == "unknown"
    seed = seed_control_data()
    with db_factory() as db:
        healthy(db, now)
        profile = VpnProfile(device_name="synthetic", mode="aws-direct", created_by_id=seed["user_id"],
            created_at=now - timedelta(minutes=5), expires_at=now + timedelta(days=1))
        db.add(profile); db.flush()
        job = ProfileJob(profile_id=profile.id, requested_by_id=seed["user_id"], kind="issue",
                         status="queued", created_at=now - timedelta(minutes=5))
        db.add(job); db.flush()
        assert build_overview(db, Settings(), lifecycle(now), now).operations[0].delayed == 0
        job.created_at -= timedelta(microseconds=1)
        db.flush()
        assert build_overview(db, Settings(), lifecycle(now), now).operations[0].delayed == 1
