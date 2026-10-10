"""Supervise worker lifetimes without retrying operations or exposing exceptions."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .models import utcnow


@dataclass(frozen=True)
class StepSnapshot:
    started_at: datetime | None = None
    completed_at: datetime | None = None
    succeeded_at: datetime | None = None
    in_progress: bool = False
    error_code: str | None = None


class StepObservation:
    """Replace immutable snapshots on the event loop; never retain exceptions."""
    def __init__(self):
        self.snapshot = StepSnapshot()

    def start(self):
        previous = self.snapshot
        self.snapshot = StepSnapshot(utcnow(), previous.completed_at, previous.succeeded_at,
                                     True, previous.error_code)

    def finish(self, failed=False):
        previous = self.snapshot
        now = utcnow()
        self.snapshot = StepSnapshot(previous.started_at, now,
                                     previous.succeeded_at if failed else now,
                                     False, "step_failed" if failed else None)


async def run_periodic_step(
    step: Callable[[], object],
    stopping: asyncio.Event,
    interval_seconds: float,
    *,
    continue_on_error: bool = False,
    observation: StepObservation | None = None,
) -> None:
    """Run blocking steps serially and wake promptly for graceful shutdown."""
    while not stopping.is_set():
        if observation is not None:
            observation.start()
        try:
            await asyncio.to_thread(step)
        except Exception:
            if observation is not None:
                observation.finish(failed=True)
            if not continue_on_error:
                raise
            # Retry only when the worker's durable state permits it. Exception
            # strings can contain private DB/PKI inputs and must not be logged.
        else:
            if observation is not None:
                observation.finish()
        try:
            await asyncio.wait_for(stopping.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass


class Worker(Protocol):
    async def run(self) -> None: ...
    def stop(self) -> None: ...


@dataclass
class WorkerState:
    worker: Worker
    task: asyncio.Task[None] | None = None
    started: bool = False
    error_code: str | None = None


class WorkerLifecycle:
    def __init__(self, workers: dict[str, Worker]):
        self.states = {name: WorkerState(worker) for name, worker in workers.items()}
        self.stopping = False

    async def _run(self, state: WorkerState) -> None:
        state.started = True
        try:
            await state.worker.run()
        except asyncio.CancelledError:
            if not self.stopping:
                state.error_code = "worker_cancelled"
            raise
        except Exception:
            # Keep only a fixed code; exception strings can contain private inputs.
            state.error_code = "worker_failed"
        else:
            if not self.stopping:
                state.error_code = "worker_stopped"

    def start(self) -> None:
        if self.stopping or any(state.task is not None for state in self.states.values()):
            raise RuntimeError("worker lifecycle already started or stopped")
        for name, state in self.states.items():
            state.task = asyncio.create_task(self._run(state), name=name)

    @property
    def ready(self) -> bool:
        return not self.stopping and bool(self.states) and all(
            state.started and state.task is not None and not state.task.done()
            and state.error_code is None for state in self.states.values()
        )

    async def stop(self) -> None:
        self.stopping = True
        for state in self.states.values():
            try:
                state.worker.stop()
            except Exception:
                state.error_code = "worker_stop_failed"
        # Do not cancel to_thread work: stop waits for its operation to finish.
        await asyncio.gather(
            *(state.task for state in self.states.values() if state.task is not None),
            return_exceptions=True,
        )
