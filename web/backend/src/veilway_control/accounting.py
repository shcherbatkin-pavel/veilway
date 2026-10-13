"""Accounting stages sharing the caller's agent lock and database transaction."""
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib

from fastapi import HTTPException
from sqlalchemy import select, update

from .models import (AnalyticsAgent, AnalyticsCoverage, AnalyticsOwner, AnalyticsReceipt, AnalyticsSource,
    DailyUsage, ProfileOwnership, ProfileUsage, VpnProfile, VpnSession, as_utc)


def daily(db, session, day):
    key = str(session.profile_id) if session.profile_id else 'unmatched'
    owner = str(session.owner_id) if session.owner_id else 'unassigned'
    row = db.scalar(select(DailyUsage).where(DailyUsage.day == day, DailyUsage.mode == session.mode,
                                            DailyUsage.profile_key == key, DailyUsage.owner_key == owner))
    if row is None:
        row = DailyUsage(day=day, mode=session.mode, profile_key=key, owner_key=owner,
            profile_id=session.profile_id, owner_id=session.owner_id, sessions_started=0, sessions_closed=0,
            interrupted=0, reconnects=0, duration_seconds=0, bytes_received=0, bytes_sent=0)
        db.add(row)
    return row


def match_profile(db, data):
    if data.certificate_serial:
        matches = db.scalars(select(VpnProfile).where(VpnProfile.mode == data.mode,
            VpnProfile.certificate_serial == data.certificate_serial).limit(2)).all()
        profile = matches[0] if len(matches) == 1 else None
        if data.profile_id is not None and profile and profile.id != data.profile_id:
            raise HTTPException(409, 'session identity conflict')
        return profile
    profile = db.get(VpnProfile, data.profile_id) if data.profile_id else None
    return profile if profile and profile.mode == data.mode and profile.certificate_serial else None


def resolve_session(db, node, data, kind):
    session = db.get(VpnSession, data.session_id)
    new = session is None
    if new:
        profile = match_profile(db, data)
        ownership = db.scalar(select(ProfileOwnership).where(ProfileOwnership.profile_id == profile.id,
            ProfileOwnership.assigned_at <= data.started_at).order_by(ProfileOwnership.assigned_at.desc(), ProfileOwnership.id.desc()).limit(1)) if profile else None
        session = VpnSession(id=data.session_id, node=node, mode=data.mode, epoch=data.epoch,
            certificate_serial=data.certificate_serial, profile_id=profile.id if profile else None,
            owner_id=ownership.owner_id if ownership else None, started_at=data.started_at,
            last_seen_at=data.started_at, state='open', recovered=kind != 'start', present=False, reconnect=False,
            bytes_received=0, bytes_sent=0)
        db.add(session)
    else:
        validate_identity(session, node, data)
    return session, new


def validate_identity(session, node, data):
    if (session.node != node or session.mode != data.mode or session.epoch != data.epoch
          or as_utc(session.started_at) != data.started_at
          or session.certificate_serial and data.certificate_serial and session.certificate_serial != data.certificate_serial
          or data.profile_id and session.profile_id and data.profile_id != session.profile_id):
        raise HTTPException(409, 'session identity conflict')


@dataclass(frozen=True)
class ObservationDelta:
    previous_seen: datetime
    last_seen: datetime
    bytes_received: int
    bytes_sent: int


def observation_delta(previous_seen, observed_at, received, sent, observed_received, observed_sent):
    """Older observations and counter resets never reduce or duplicate totals."""
    return ObservationDelta(previous_seen, max(previous_seen, observed_at),
        max(0, observed_received - received), max(0, observed_sent - sent))


def record_traffic(db, session, data, delta, now, settings):
    # Traffic is accounted at observation time, never claimed as exact packet time.
    row = daily(db, session, data.observed_at.date())
    row.bytes_received += delta.bytes_received
    row.bytes_sent += delta.bytes_sent
    begin = max(delta.previous_seen.date(), (now - timedelta(days=settings.analytics_aggregate_days - 1)).date())
    for offset in range(max(0, (data.observed_at.date() - begin).days) + 1):
        daily(db, session, begin + timedelta(days=offset))
    return row


def record_start(db, session, data, new):
    if new:
        daily(db, session, data.started_at.date()).sessions_started += 1
        if session.profile_id and db.scalar(select(VpnSession.id).where(
            VpnSession.profile_id == session.profile_id, VpnSession.id != session.id,
            VpnSession.ended_at <= data.started_at, VpnSession.ended_at >= data.started_at - timedelta(seconds=120)).limit(1)):
            session.reconnect = True
            daily(db, session, data.started_at.date()).reconnects += 1


def record_completion(db, session, data, kind, row):
    if kind == 'end' and session.state != 'closed':
        session.ended_at = data.observed_at
        session.duration_seconds = int((data.observed_at - data.started_at).total_seconds())
        row.sessions_closed += 1
        row.duration_seconds += session.duration_seconds
        session.state, session.present = 'closed', False
        if session.profile_id:
            following = db.scalar(select(VpnSession).where(VpnSession.profile_id == session.profile_id,
                VpnSession.id != session.id, VpnSession.started_at >= data.observed_at,
                VpnSession.started_at <= data.observed_at + timedelta(seconds=120))
                .order_by(VpnSession.started_at, VpnSession.id).limit(1))
            if following and not following.reconnect:
                following.reconnect = True
                daily(db, following, as_utc(following.started_at).date()).reconnects += 1
    elif kind == 'interrupted' and session.state == 'open':
        session.state, session.present = 'interrupted', False
        row.interrupted += 1


def record_usage(db, session, data, new, delta):
    if session.profile_id:
        usage = db.get(ProfileUsage, session.profile_id)
        if usage is None:
            usage = ProfileUsage(profile_id=session.profile_id, first_connected_at=data.started_at,
                last_connected_at=data.started_at, last_seen_at=session.last_seen_at, sessions=0, bytes_received=0, bytes_sent=0)
            db.add(usage)
        usage.first_connected_at = min(as_utc(usage.first_connected_at), data.started_at)
        usage.last_connected_at = max(as_utc(usage.last_connected_at), data.started_at)
        usage.last_seen_at = max(as_utc(usage.last_seen_at), as_utc(session.last_seen_at))
        usage.sessions += int(new)
        usage.bytes_received += delta.bytes_received
        usage.bytes_sent += delta.bytes_sent
    if session.owner_id:
        owner = db.get(AnalyticsOwner, session.owner_id)
        if owner is None:
            db.add(AnalyticsOwner(owner_id=session.owner_id, first_connected_at=data.started_at))
        else:
            owner.first_connected_at = min(as_utc(owner.first_connected_at), data.started_at)


def account(db, node, data, kind, now, settings):
    session, new = resolve_session(db, node, data, kind)
    if data.certificate_serial and session.certificate_serial is None:
        session.certificate_serial = data.certificate_serial
    if kind == 'start':
        session.recovered = False
    delta = observation_delta(as_utc(session.last_seen_at), data.observed_at,
        session.bytes_received, session.bytes_sent, data.bytes_received, data.bytes_sent)
    session.last_seen_at = delta.last_seen
    session.bytes_received += delta.bytes_received
    session.bytes_sent += delta.bytes_sent
    row = record_traffic(db, session, data, delta, now, settings)
    record_start(db, session, data, new)
    record_completion(db, session, data, kind, row)
    record_usage(db, session, data, new, delta)
    return session


def validate_report(payload, permitted, now):
    if any(e.mode not in permitted for e in payload.events) or any(s.mode not in permitted for s in payload.sources):
        raise HTTPException(403, 'source not configured for agent')
    for data in [*payload.events, *(s for r in payload.sources for s in r.sessions)]:
        if data.observed_at > now + timedelta(seconds=60) or data.started_at > now + timedelta(seconds=60):
            raise HTTPException(422, 'invalid accounting clock')


def ingest_events(db, agent, payload, permitted, settings, now, lower):
    for event in payload.events:
        digest = hashlib.sha256(event.model_dump_json().encode()).digest()
        receipt = db.get(AnalyticsReceipt, event.id)
        if receipt:
            if receipt.node != agent.slug or receipt.payload_hash != digest:
                raise HTTPException(409, 'event replay conflict')
            continue
        if event.observed_at < lower:
            continue  # Obsolete retries cannot recreate pruned sessions or traffic.
        account(db, agent.slug, event, event.kind, now, settings)
        source = permitted[event.mode]
        source.first_observed_at = min(as_utc(source.first_observed_at), event.observed_at) if source.first_observed_at else event.observed_at
        db.add(AnalyticsReceipt(id=event.id, node=agent.slug, payload_hash=digest, observed_at=event.observed_at))


def ingest_sources(db, agent, payload, permitted, settings, now, lower):
    for report in payload.sources:
        source = permitted[report.mode]
        source.received_at = now
        source.capture_errors = max(source.capture_errors, report.capture_errors)
        if report.status == 'unavailable':
            source.available = False
            continue
        if report.observed_at > now + timedelta(seconds=60):
            raise HTTPException(422, 'invalid accounting clock')
        if report.observed_at < lower or source.observed_at and report.observed_at <= as_utc(source.observed_at):
            continue
        db.execute(update(VpnSession).where(VpnSession.node == agent.slug, VpnSession.mode == report.mode).values(present=False))
        source.epoch, source.observed_at, source.available = report.epoch, report.observed_at, True
        source.connection_count = report.connection_count
        source.first_observed_at = min(as_utc(source.first_observed_at), report.observed_at) if source.first_observed_at else report.observed_at
        coverage = db.get(AnalyticsCoverage, (report.observed_at.date(), report.mode))
        if coverage is None:
            coverage = AnalyticsCoverage(day=report.observed_at.date(), mode=report.mode, observations=0)
            db.add(coverage)
        coverage.observations += 1
        for data in report.sessions:
            session = account(db, agent.slug, data, 'snapshot', now, settings)
            session.snapshot_at = report.observed_at
            session.present = session.state == 'open'


def ingest(db, agent, payload, settings, now):
    # A node lock serializes retries/snapshots, including receipt and counter updates.
    agent = db.scalar(select(AnalyticsAgent).where(AnalyticsAgent.slug == agent.slug).with_for_update())
    permitted = {s.mode: s for s in db.scalars(select(AnalyticsSource).where(AnalyticsSource.node == agent.slug))}
    validate_report(payload, permitted, now)
    lower = now - timedelta(days=settings.analytics_session_days)
    ingest_events(db, agent, payload, permitted, settings, now, lower)
    ingest_sources(db, agent, payload, permitted, settings, now, lower)
    agent.last_contact_at = now
    if agent.pruned_at is None or as_utc(agent.pruned_at) < now - timedelta(days=1):
        from .analytics_retention import prune
        prune(db, settings, now, [agent.slug])
        agent.pruned_at = now
    db.commit()
