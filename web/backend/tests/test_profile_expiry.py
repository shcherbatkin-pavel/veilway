from datetime import datetime, timedelta, timezone
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from veilway_control import profiles
from veilway_control.models import ProfileAuditEvent, ProfileJob, User, VpnProfile
from veilway_control.schemas import ProfileCreateRequest


NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def request(**values):
    return ProfileCreateRequest(idempotency_key=uuid.uuid4(), device_name="Test device", mode="aws-direct", **values)


@pytest.mark.parametrize("days", [None, 1, 7, 365])
def test_duration_and_default_are_relative_to_supplied_time(days):
    payload = request(duration_days=days)
    assert profiles.profile_expiry(payload, now=NOW) == NOW + timedelta(days=365 if days is None else days)
    assert payload.duration_days == days


def test_absolute_expiry_is_normalized_to_utc_without_mutating_request():
    expires = datetime(2026, 10, 10, 15, tzinfo=timezone(timedelta(hours=3)))
    payload = request(expires_at=expires)
    result = profiles.profile_expiry(payload, now=NOW)
    assert result == datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    assert result.tzinfo is timezone.utc
    assert payload.expires_at is expires


@pytest.mark.parametrize("expires", [NOW, NOW - timedelta(seconds=1)])
def test_expiry_must_be_strictly_after_supplied_time(expires):
    with pytest.raises(HTTPException) as rejected:
        profiles.profile_expiry(request(expires_at=expires), now=NOW)
    assert rejected.value.status_code == 422
    assert rejected.value.detail == "expiry must be in the future"


@pytest.mark.parametrize("days,now", [(10**15, NOW), (1, datetime(9999, 12, 31, tzinfo=timezone.utc))])
def test_duration_and_calendar_overflow_keep_fixed_validation_error(days, now):
    with pytest.raises(HTTPException) as rejected:
        profiles.profile_expiry(request(duration_days=days), now=now)
    assert rejected.value.status_code == 422
    assert rejected.value.detail == "expiry is outside the supported calendar"


def test_idempotency_digest_preserves_default_duration_and_timezone_equivalence():
    assert profiles.request_digest(request()) == profiles.request_digest(request(duration_days=365))
    assert profiles.request_digest(request(duration_days=7)) != profiles.request_digest(request())
    utc = NOW + timedelta(days=1)
    local = utc.astimezone(timezone(timedelta(hours=3)))
    assert profiles.request_digest(request(expires_at=utc)) == profiles.request_digest(request(expires_at=local))


def test_durable_replay_precedes_expiry_validation(db_factory, seed_control_data, monkeypatch):
    actor_id = seed_control_data()["user_id"]
    payload = request(expires_at=NOW + timedelta(days=1))
    monkeypatch.setattr(profiles, "utcnow", lambda: NOW)
    with db_factory() as db:
        actor = db.get(User, actor_id)
        first_profile, first_job = profiles.create_profile(db, actor, payload)
        monkeypatch.setattr(profiles, "utcnow", lambda: NOW + timedelta(days=2))
        repeated_profile, repeated_job = profiles.create_profile(db, actor, payload)
        assert (repeated_profile.id, repeated_job.id) == (first_profile.id, first_job.id)
        for model in (VpnProfile, ProfileJob, ProfileAuditEvent):
            assert db.scalar(select(func.count()).select_from(model)) == 1
