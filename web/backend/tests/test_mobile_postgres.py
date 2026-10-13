"""Atomic one-use grants on a disposable PostgreSQL, never production."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from threading import Barrier

from sqlalchemy import select, func
from test_profile_postgres import profile_db
from test_migrations import postgres_connection
from veilway_control.mobile_api import TokenRequest, token, rate_limit
from veilway_control.mobile_models import MobileDeviceAttempt, MobileSession
from veilway_control.models import utcnow
from veilway_control.security import hash_token, new_token


def test_concurrent_exchange_issues_exactly_one_token(profile_db):
    factory, (_, owner, _) = profile_db
    code = new_token()
    with factory() as db:
        db.add(MobileDeviceAttempt(device_hash=hash_token(code), code_hash=hash_token("ABCD-2345"),
            user_id=owner, expires_at=utcnow() + timedelta(minutes=10), next_poll_at=utcnow() - timedelta(seconds=1), poll_seconds=5))
        db.commit()
    barrier = Barrier(4)
    def exchange(_):
        with factory() as db:
            barrier.wait(timeout=10)
            response = token(TokenRequest(device_code=code), db)
            return response.status_code, json.loads(response.body)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(exchange, range(4)))
    assert sum(status == 200 for status, _ in results) == 1
    assert all(body.get("error") == "expired_token" for status, body in results if status != 200)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(MobileSession)) == 1
        assert db.scalar(select(func.count()).select_from(MobileDeviceAttempt)) == 0


def test_concurrent_rate_limit_has_no_lost_updates(profile_db):
    factory, _ = profile_db
    barrier = Barrier(4)
    def hit(_):
        from fastapi import HTTPException
        with factory() as db:
            barrier.wait(timeout=10)
            try:
                rate_limit(db, "race", 1, 86400)
                return 200
            except HTTPException as error:
                return error.status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sorted(pool.map(hit, range(4))) == [200, 429, 429, 429]
