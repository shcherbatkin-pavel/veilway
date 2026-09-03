from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from .api import router
from .config import get_settings
from .database import get_session_factory
from .orchestrator import RestartWorker


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    worker = RestartWorker(settings, get_session_factory())
    task = asyncio.create_task(worker.run(), name="restart-worker")
    app.state.restart_worker = worker
    try:
        yield
    finally:
        worker.stop()
        await task


settings = get_settings()
app = FastAPI(
    title="Veilway Control API",
    version="0.1.0",
    docs_url=None if settings.environment == "production" else "/api/docs",
    redoc_url=None,
    openapi_url=None if settings.environment == "production" else "/api/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=[settings.public_host, "localhost", "testserver"],
)
app.include_router(router)


@app.get("/api/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}

