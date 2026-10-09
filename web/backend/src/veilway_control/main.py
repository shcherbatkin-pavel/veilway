from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from .api import router
from .config import get_settings
from .database import get_session_factory
from .orchestrator import RestartWorker
from .profile_worker import ProfileWorker
from .crl import CrlWorker
from .headers import ApiNoStoreMiddleware, safe_validation_error
from .workers import WorkerLifecycle
from fastapi.exceptions import RequestValidationError


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    factory = get_session_factory()
    app.state.restart_worker = RestartWorker(settings, factory)
    app.state.profile_worker = ProfileWorker(settings, factory)
    app.state.crl_worker = CrlWorker(settings, factory)
    lifecycle = WorkerLifecycle({
        "restart-worker": app.state.restart_worker,
        "profile-worker": app.state.profile_worker,
        "crl-worker": app.state.crl_worker,
    })
    app.state.worker_lifecycle = lifecycle
    lifecycle.start()
    try:
        yield
    finally:
        await lifecycle.stop()


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
app.add_middleware(ApiNoStoreMiddleware)
app.add_exception_handler(RequestValidationError, safe_validation_error)
app.include_router(router)


@app.get("/api/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/readyz", include_in_schema=False)
def readyz(request: Request) -> JSONResponse:
    lifecycle = getattr(request.app.state, "worker_lifecycle", None)
    ready = lifecycle is not None and lifecycle.ready
    return JSONResponse({"status": "ready" if ready else "unavailable"},
                        status_code=200 if ready else 503)
