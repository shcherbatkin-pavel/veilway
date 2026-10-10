from __future__ import annotations

from datetime import timedelta
from typing import cast

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from urllib.parse import parse_qs, urlsplit

from oidc_helpers import FakeGoogleClient, TestSettings

from veilway_control.api import create_restart_job, router
from veilway_control.config import Settings, get_settings
from veilway_control.database import get_db
from veilway_control.models import User, UserSession, VmHeartbeat, utcnow
from veilway_control.schemas import RestartCreateRequest
from veilway_control.security import AuthenticatedUser
from veilway_control.oidc import get_google_client
from veilway_control.headers import ApiNoStoreMiddleware, safe_validation_error
from fastapi.exceptions import RequestValidationError


def build_client(db_factory, *, settings=None, oidc_client=None) -> TestClient:
    app = FastAPI()
    app.add_middleware(ApiNoStoreMiddleware)
    app.add_exception_handler(RequestValidationError, safe_validation_error)
    app.include_router(router)

    def database_override():
        with db_factory() as db:
            yield db

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: settings or TestSettings(public_host="testserver")
    app.dependency_overrides[get_google_client] = lambda: oidc_client or provider
    provider = FakeGoogleClient()
    return TestClient(app, base_url="https://testserver")


def login(client: TestClient) -> str:
    start = client.get("/api/v1/auth/google/start", follow_redirects=False)
    assert start.status_code == 303
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    response = client.get(
        "/api/v1/auth/google/callback", params={"state": state, "code": "test-code"},
        follow_redirects=False,
    )
    assert response.status_code == 303 and response.headers["location"] == "/"
    cookies = response.headers.get_list("set-cookie")
    session = next(cookie for cookie in cookies if cookie.startswith("__Host-veilway_session="))
    assert "Secure" in session and "HttpOnly" in session and "SameSite=strict" in session
    current = client.get("/api/v1/auth/session")
    assert current.status_code == 200
    return current.json()["csrf_token"]


def test_login_session_csrf_and_logout(db_factory, seed_control_data) -> None:
    seed_control_data()
    with build_client(db_factory) as client:
        invalid = client.post(
            "/api/v1/auth/login",
            json={"login": "operator", "password": "wrong"},
        )
        assert invalid.status_code == 404

        login(client)
        current = client.get("/api/v1/auth/session")
        assert current.status_code == 200
        rotated_csrf = current.json()["csrf_token"]

        assert client.post("/api/v1/auth/logout").status_code == 403
        assert (
            client.post(
                "/api/v1/auth/logout",
                headers={"X-CSRF-Token": rotated_csrf},
            ).status_code
            == 204
        )
        assert client.get("/api/v1/auth/session").status_code == 401


def test_heartbeat_requires_exact_slug_and_token(db_factory, seed_control_data) -> None:
    seed_control_data(with_heartbeats=False)
    payload = {
        "boot_id": "00000000-0000-4000-8000-000000000099",
        "uptime_seconds": 10,
        "containers": {
            "veilway-openvpn": "healthy",
            "veilway-unbound": "healthy",
        },
    }
    with build_client(db_factory) as client:
        assert (
            client.post(
                "/api/v1/agents/legacy/heartbeat",
                json=payload,
                headers={"Authorization": f"Bearer {'a' * 40}"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/agents/aws-direct/heartbeat",
                json=payload,
                headers={"Authorization": "Bearer wrong"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/agents/aws-direct/heartbeat",
                json=payload,
                headers={"Authorization": f"Bearer {'a' * 40}"},
            ).status_code
            == 204
        )

    with db_factory() as db:
        heartbeat = db.scalar(select(VmHeartbeat))
        assert heartbeat is not None
        assert heartbeat.healthy is True
        assert heartbeat.containers == payload["containers"]


def test_restart_allowlist_order_and_single_active_job(
    db_factory, seed_control_data
) -> None:
    seed_control_data()
    with build_client(db_factory) as client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}

        unknown = client.post(
            "/api/v1/restart-jobs",
            json={"targets": ["legacy"]},
            headers=headers,
        )
        assert unknown.status_code == 422

        created = client.post(
            "/api/v1/restart-jobs",
            json={"targets": ["yc-direct", "aws-direct"]},
            headers=headers,
        )
        assert created.status_code == 202
        assert [item["slug"] for item in created.json()["targets"]] == [
            "aws-direct",
            "yc-direct",
        ]

        duplicate = client.post(
            "/api/v1/restart-jobs",
            json={"targets": ["aws-direct"]},
            headers=headers,
        )
        assert duplicate.status_code == 409

        vms = client.get("/api/v1/vpn-vms")
        assert vms.status_code == 200
        serialized = vms.text
        assert "i-0123456789abcdef0" not in serialized
        assert "fhm0123456789abcdef0" not in serialized


def test_restart_requires_fresh_healthy_heartbeat(
    db_factory, seed_control_data
) -> None:
    seed_control_data()
    with db_factory() as db:
        admin = db.scalar(select(User))
        heartbeat = db.scalars(select(VmHeartbeat)).first()
        assert admin is not None
        assert heartbeat is not None
        heartbeat.healthy = False
        db.commit()
        authenticated = AuthenticatedUser(
            user=admin,
            session=cast(UserSession, None),
        )
        with pytest.raises(HTTPException) as unhealthy:
            create_restart_job(
                RestartCreateRequest(targets=["aws-direct"]),
                authenticated,
                db,
                Settings(public_host="testserver"),
            )
        assert unhealthy.value.status_code == 409

        heartbeat.healthy = True
        heartbeat.received_at = utcnow() - timedelta(seconds=61)
        db.commit()
        with pytest.raises(HTTPException) as stale:
            create_restart_job(
                RestartCreateRequest(targets=["aws-direct"]),
                authenticated,
                db,
                Settings(public_host="testserver"),
            )
        assert stale.value.status_code == 409


@pytest.mark.parametrize("age,accepted", [(60, True), (60.000001, False)])
def test_heartbeat_freshness_boundary(db_factory, seed_control_data, monkeypatch, age, accepted):
    seed_control_data()
    now = utcnow()
    monkeypatch.setattr("veilway_control.api.utcnow", lambda: now)
    with db_factory() as db:
        for heartbeat in db.scalars(select(VmHeartbeat)):
            heartbeat.received_at = now - timedelta(seconds=age)
        db.commit()
    with build_client(db_factory) as client:
        csrf = login(client)
        vms = client.get("/api/v1/vpn-vms").json()
        assert all(vm["state"] == ("healthy" if accepted else "degraded") for vm in vms)
        response = client.post("/api/v1/restart-jobs", json={"targets": ["aws-direct"]}, headers={"X-CSRF-Token": csrf})
        assert response.status_code == (202 if accepted else 409)
        if accepted:
            body = response.json()
            assert set(body) == {"id", "status", "created_at", "started_at", "finished_at", "error_code", "targets"}
            assert set(body["targets"][0]) == {"slug", "position", "status", "dispatched_at", "recovered_at", "error_code"}
            detail = client.get(f"/api/v1/restart-jobs/{body['id']}").json()
            # SQLite reloads timezone-aware columns as naive UTC datetimes.
            assert detail["created_at"].removesuffix("Z") == body["created_at"].removesuffix("Z")
            assert {key: value for key, value in detail.items() if key != "created_at"} == {key: value for key, value in body.items() if key != "created_at"}
            assert client.get("/api/v1/restart-jobs").json() == [detail]
        else:
            assert response.json() == {"detail": "target heartbeat is not fresh and healthy"}
