"""Supervise worker lifetimes without retrying operations or exposing exceptions."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


async def run_periodic_step(
    step: Callable[[], object],
    stopping: asyncio.Event,
    interval_seconds: float,
    *,
    continue_on_error: bool = False,
) -> None:
    """Run blocking steps serially and wake promptly for graceful shutdown."""
    while not stopping.is_set():
        try:
            await asyncio.to_thread(step)
        except Exception:
            if not continue_on_error:
                raise
            # Retry only when the worker's durable state permits it. Exception
            # strings can contain private DB/PKI inputs and must not be logged.
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
