"""Stage boundaries must preserve accounting and the caller's transaction."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select, func

from test_analytics import scene, event, frame, apply, data
from test_profile_postgres import profile_db
from test_migrations import postgres_connection
from veilway_control.accounting import observation_delta, ingest
from veilway_control.accounting_schemas import AccountingReport
from veilway_control.analytics_queries import period, build_profile_page, build_session_page
from veilway_control.config import Settings
from veilway_control.models import (AnalyticsAgent, AnalyticsReceipt, AnalyticsSource, VpnSession, DailyUsage,
    VpnProfile, ProfileOwnership, utcnow)
from veilway_control.security import hash_token


def test_delta_retains_high_water_marks_on_old_observations_and_counter_reset(scene):
    now = scene['now']
    for observed, rx, tx, expected in [
        (now, 150, 260, (50, 60)),
        (now - timedelta(seconds=1), 100, 200, (0, 0)),
        (now - timedelta(seconds=1), 5, 10, (0, 0)),
    ]:
        delta = observation_delta(now, observed, 100, 200, rx, tx)
        assert delta.last_seen == now
        assert (delta.bytes_received, delta.bytes_sent) == expected


def test_conflicting_later_event_leaves_earlier_updates_in_caller_transaction(db_factory, scene):
    started = scene['now'] - timedelta(seconds=10)
    first = event(scene, 'start', started=started, observed=started)
    conflict = event(scene, 'end', started=started - timedelta(seconds=1), rx=100)
    payload = AccountingReport(events=[first, conflict], sources=[frame(scene)])
    with db_factory() as db:
        with pytest.raises(HTTPException) as rejected:
            ingest(db, db.get(AnalyticsAgent, 'aws-direct'), payload, Settings(), scene['now'])
        assert rejected.value.status_code == 409
        db.rollback()
    with db_factory() as db:
        assert db.get(VpnSession, scene['session']) is None
        assert db.scalar(select(func.count()).select_from(AnalyticsReceipt)) == 0
        assert db.scalar(select(func.count()).select_from(DailyUsage)) == 0


def test_receipt_conflict_does_not_change_committed_counters(db_factory, scene):
    original = event(scene, 'end', rx=10, tx=20)
    apply(db_factory, scene, [original])
    changed = dict(original, bytes_received=999)
    with db_factory() as db:
        with pytest.raises(HTTPException) as rejected:
            ingest(db, db.get(AnalyticsAgent, 'aws-direct'), AccountingReport(events=[changed], sources=[frame(scene)]), Settings(), scene['now'])
        assert rejected.value.status_code == 409
        db.rollback()
    with db_factory() as db:
        session = db.get(VpnSession, scene['session'])
        assert session.bytes_received == 10 and session.bytes_sent == 20


def test_query_services_keep_pagination_and_historical_owner(db_factory, scene):
    apply(db_factory, scene, reports=[frame(scene, [data(scene, rx=10, tx=20)])])
    with db_factory() as db:
        page = build_profile_page(db, Settings(), scene['now'], 30, 'aws-direct', True, 1, 0)
        assert page.total == 1 and page.items[0].profile_id == scene['profile']
        assert page.items[0].online is True
        assert build_profile_page(db, Settings(), scene['now'], 30, 'aws-direct', True, 1, 1).items == []
        sessions = build_session_page(db, Settings(), scene['now'], 30, 'aws-direct', scene['profile'], 1, 0)
        assert sessions.total == 1 and sessions.items[0].online is True
        assert build_session_page(db, Settings(), scene['now'], 30, 'aws-direct', uuid.uuid4()).total == 0
        assert build_session_page(db, Settings(), scene['now'], 30, 'aws-direct', scene['profile'], 1, 1).items == []
        assert sessions.retained_since == scene['now'] - timedelta(days=Settings().analytics_session_days)


def test_period_starts_at_utc_midnight_and_includes_current_day(scene):
    now = scene['now'].replace(hour=23, minute=59, second=59)
    assert period(now, 1) == now.replace(hour=0, minute=0, second=0)
    assert period(now, 7) == now.replace(hour=0, minute=0, second=0) - timedelta(days=6)


def test_concurrent_reports_share_agent_lock_and_account_one_receipt(profile_db):
    factory, (admin_id, owner_id, _) = profile_db
    now = utcnow().replace(microsecond=0)
    with factory() as db:
        profile = VpnProfile(device_name='Synthetic concurrent profile', mode='aws-direct', owner_id=owner_id,
            created_by_id=admin_id, status='active', certificate_serial='7A', created_at=now-timedelta(days=1), expires_at=now+timedelta(days=1))
        db.add(profile); db.flush()
        db.add(ProfileOwnership(profile_id=profile.id, owner_id=owner_id, assigned_at=profile.created_at))
        db.add(AnalyticsAgent(slug='aws-direct', token_hash=hash_token('synthetic-accounting-agent')))
        source = db.get(AnalyticsSource, 'aws-direct')
        source.node = 'aws-direct'; source.configured_at = now-timedelta(days=1)
        db.commit()
        context = dict(now=now, profile=profile.id, owner=owner_id, epoch=uuid.uuid4(), session=uuid.uuid4())
    payload = AccountingReport(events=[event(context, 'end', rx=123, tx=456)], sources=[frame(context)])
    barrier = Barrier(3)
    def submit(_):
        with factory() as db:
            agent = db.get(AnalyticsAgent, 'aws-direct')
            barrier.wait(timeout=10)
            ingest(db, agent, payload, Settings(), now)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(submit, range(3)))
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(AnalyticsReceipt)) == 1
        assert db.scalar(select(func.count()).select_from(VpnSession)) == 1
        session = db.get(VpnSession, context['session'])
        assert session.state == 'closed' and session.bytes_received == 123 and session.bytes_sent == 456
        row = db.scalar(select(DailyUsage).where(DailyUsage.profile_id == context['profile']))
        assert row.sessions_started == 1 and row.sessions_closed == 1
        assert row.bytes_received == 123 and row.bytes_sent == 456
