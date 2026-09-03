from __future__ import annotations

from datetime import timedelta
from typing import cast

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from veilway_control.api import create_restart_job, router
from veilway_control.config import Settings, get_settings
from veilway_control.database import get_db
from veilway_control.models import Admin, AdminSession, VmHeartbeat, utcnow
from veilway_control.schemas import RestartCreateRequest
from veilway_control.security import AuthenticatedAdmin


def build_client(db_factory) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    def database_override():
        with db_factory() as db:
            yield db

    app.dependency_overrides[get_db] = database_override
    app.dependency_overrides[get_settings] = lambda: Settings(public_host="testserver")
    return TestClient(app, base_url="https://testserver")


def login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"login": "operator", "password": "correct horse battery staple"},
    )
    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    return response.json()["csrf_token"]


def test_login_session_csrf_and_logout(db_factory, seed_control_data) -> None:
    seed_control_data()
    with build_client(db_factory) as client:
        invalid = client.post(
            "/api/v1/auth/login",
            json={"login": "operator", "password": "wrong"},
        )
        assert invalid.status_code == 401

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
        admin = db.scalar(select(Admin))
        heartbeat = db.scalars(select(VmHeartbeat)).first()
        assert admin is not None
        assert heartbeat is not None
        heartbeat.healthy = False
        db.commit()
        authenticated = AuthenticatedAdmin(
            admin=admin,
            session=cast(AdminSession, None),
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
