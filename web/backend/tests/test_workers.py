from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from veilway_control.workers import WorkerLifecycle


class QuietWorker:
    def __init__(self):
        self.stopping = asyncio.Event()
        self.finished = False

    async def run(self):
        await self.stopping.wait()
        self.finished = True

    def stop(self):
        self.stopping.set()


@pytest.mark.parametrize("name", ["restart-worker", "profile-worker", "crl-worker"])
@pytest.mark.parametrize("failure", [True, False])
def test_unexpected_exit_is_fixed_safe_state_and_does_not_restart(name, failure, caplog):
    async def scenario():
        class ExitingWorker(QuietWorker):
            calls = 0

            async def run(self):
                self.calls += 1
                if failure:
                    raise RuntimeError("synthetic-private-exception")

        workers = {key: QuietWorker() for key in ("restart-worker", "profile-worker", "crl-worker")}
        workers[name] = ExitingWorker()
        lifecycle = WorkerLifecycle(workers)
        assert not lifecycle.ready
        lifecycle.start()
        await asyncio.sleep(0)
        assert not lifecycle.ready
        assert lifecycle.states[name].error_code == ("worker_failed" if failure else "worker_stopped")
        await lifecycle.stop()
        assert workers[name].calls == 1
        assert all(worker.finished for key, worker in workers.items() if key != name)

    asyncio.run(scenario())
    assert "synthetic-private-exception" not in caplog.text


def test_shutdown_waits_for_in_flight_thread_after_another_worker_failed():
    async def scenario():
        entered, release = threading.Event(), threading.Event()

        class BlockingWorker(QuietWorker):
            async def run(self):
                def operation():
                    entered.set()
                    release.wait(timeout=5)
                await asyncio.to_thread(operation)
                self.finished = True

        class FailedWorker(QuietWorker):
            async def run(self):
                raise RuntimeError("synthetic-private-exception")

        blocking, quiet = BlockingWorker(), QuietWorker()
        lifecycle = WorkerLifecycle({"restart-worker": FailedWorker(), "profile-worker": blocking, "crl-worker": quiet})
        lifecycle.start()
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert entered.is_set()
            shutdown = asyncio.create_task(lifecycle.stop())
            await asyncio.sleep(0)
            assert blocking.stopping.is_set() and quiet.stopping.is_set()
            assert not lifecycle.ready
            assert not shutdown.done()
            release.set()
            await shutdown
            assert blocking.finished and quiet.finished
        finally:
            release.set()
            await lifecycle.stop()

    asyncio.run(scenario())


def test_readiness_contract_and_liveness_are_independent(monkeypatch):
    from veilway_control.main import app

    async def scenario():
        lifecycle = WorkerLifecycle({key: QuietWorker() for key in ("restart-worker", "profile-worker", "crl-worker")})
        monkeypatch.setattr(app.state, "worker_lifecycle", lifecycle, raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get("/api/readyz")
            assert response.status_code == 503
            assert response.json() == {"status": "unavailable"}
            lifecycle.start()
            await asyncio.sleep(0)
            response = await client.get("/api/readyz")
            assert response.status_code == 200
            assert response.json() == {"status": "ready"}
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["Pragma"] == "no-cache"
            await lifecycle.stop()
            assert (await client.get("/api/readyz")).status_code == 503
            response = await client.get("/api/healthz")
            assert response.status_code == 200 and response.json() == {"status": "ok"}

    asyncio.run(scenario())
