"""Private, idempotent session accounting from explicitly configured VPN agents."""
from datetime import date, datetime, timedelta
import hashlib
import hmac
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import get_db
from .models import (AnalyticsAgent, AnalyticsCoverage, AnalyticsOwner, AnalyticsReceipt, AnalyticsSource, DailyUsage,
                     ProfileOwnership, ProfileUsage, VpnProfile, VpnSession, as_utc, utcnow)
from .security import hash_token

Mode = Literal['aws-direct', 'yc-direct', 'yc-aws-multihop']
NODE_MODES = {'aws-direct': {'aws-direct'}, 'yc-direct': {'yc-direct', 'yc-aws-multihop'}}
router = APIRouter()


class AccountingData(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: uuid.UUID
    epoch: uuid.UUID
    mode: Mode
    certificate_serial: str | None = Field(default=None, pattern=r'^[1-9A-F][0-9A-F]{0,39}$')
    profile_id: uuid.UUID | None = None
    started_at: datetime
    observed_at: datetime
    bytes_received: int = Field(ge=0, lt=2**63, strict=True)
    bytes_sent: int = Field(ge=0, lt=2**63, strict=True)

    @field_validator('started_at', 'observed_at')
    @classmethod
    def utc_timestamp(cls, value):
        if value.tzinfo is None or value.microsecond:
            raise ValueError('UTC timestamps with whole seconds required')
        return as_utc(value)

    @model_validator(mode='after')
    def chronology(self):
        if self.observed_at < self.started_at:
            raise ValueError('invalid session times')
        return self


class SessionEvent(AccountingData):
    id: uuid.UUID
    kind: Literal['start', 'end', 'interrupted']


class SourceReport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Mode
    status: Literal['available', 'unavailable']
    epoch: uuid.UUID | None = None
    observed_at: datetime | None = None
    connection_count: int | None = Field(default=None, ge=0, le=2000, strict=True)
    sessions: list[AccountingData] = Field(default_factory=list, max_length=2000)
    capture_errors: int = Field(default=0, ge=0, le=2**31-1, strict=True)

    @model_validator(mode='after')
    def shape(self):
        if self.status == 'available':
            if self.epoch is None or self.observed_at is None or self.connection_count is None:
                raise ValueError('incomplete snapshot')
            if self.observed_at.tzinfo is None or self.observed_at.microsecond:
                raise ValueError('UTC whole seconds required')
            self.observed_at = as_utc(self.observed_at)
            if len(self.sessions) > self.connection_count or len({s.session_id for s in self.sessions}) != len(self.sessions):
                raise ValueError('invalid snapshot count')
            if any(s.mode != self.mode or s.epoch != self.epoch or s.observed_at != self.observed_at for s in self.sessions):
                raise ValueError('mixed snapshot')
        elif self.sessions or self.connection_count is not None:
            raise ValueError('unavailable is not an empty snapshot')
        return self


class AccountingReport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    events: list[SessionEvent] = Field(default_factory=list, max_length=500)
    sources: list[SourceReport] = Field(min_length=1, max_length=3)

    @model_validator(mode='after')
    def unique(self):
        if len({e.id for e in self.events}) != len(self.events) or len({s.mode for s in self.sources}) != len(self.sources):
            raise ValueError('duplicate event or source')
        return self


def authenticate_agent(slug: str, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    if slug not in NODE_MODES or not authorization or not authorization.startswith('Bearer ') or len(authorization) > 263:
        raise HTTPException(401)
    agent = db.get(AnalyticsAgent, slug)
    if agent is None or not hmac.compare_digest(agent.token_hash, hash_token(authorization[7:])):
        raise HTTPException(401)
    return agent


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


def account(db, node, data, kind, now, settings):
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
    elif (session.node != node or session.mode != data.mode or session.epoch != data.epoch
          or as_utc(session.started_at) != data.started_at
          or session.certificate_serial and data.certificate_serial and session.certificate_serial != data.certificate_serial
          or data.profile_id and session.profile_id and data.profile_id != session.profile_id):
        raise HTTPException(409, 'session identity conflict')
    if data.certificate_serial and session.certificate_serial is None:
        session.certificate_serial = data.certificate_serial
    if kind == 'start':
        session.recovered = False
    previous_seen = as_utc(session.last_seen_at)
    session.last_seen_at = max(previous_seen, data.observed_at)
    rx = max(0, data.bytes_received - session.bytes_received)
    tx = max(0, data.bytes_sent - session.bytes_sent)
    session.bytes_received += rx
    session.bytes_sent += tx
    # Traffic is accounted at observation time, never claimed as exact packet time.
    row = daily(db, session, data.observed_at.date())
    row.bytes_received += rx
    row.bytes_sent += tx
    begin = max(previous_seen.date(), (now - timedelta(days=settings.analytics_aggregate_days - 1)).date())
    for offset in range(max(0, (data.observed_at.date() - begin).days) + 1):
        daily(db, session, begin + timedelta(days=offset))
    if new:
        daily(db, session, data.started_at.date()).sessions_started += 1
        if session.profile_id and db.scalar(select(VpnSession.id).where(
            VpnSession.profile_id == session.profile_id, VpnSession.id != session.id,
            VpnSession.ended_at <= data.started_at, VpnSession.ended_at >= data.started_at - timedelta(seconds=120)).limit(1)):
            session.reconnect = True
            daily(db, session, data.started_at.date()).reconnects += 1
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
        usage.bytes_received += rx
        usage.bytes_sent += tx
    if session.owner_id:
        owner = db.get(AnalyticsOwner, session.owner_id)
        if owner is None:
            db.add(AnalyticsOwner(owner_id=session.owner_id, first_connected_at=data.started_at))
        else:
            owner.first_connected_at = min(as_utc(owner.first_connected_at), data.started_at)
    return session


def ingest(db, agent, payload, settings, now):
    # A node lock serializes retries/snapshots, including receipt and counter updates.
    agent = db.scalar(select(AnalyticsAgent).where(AnalyticsAgent.slug == agent.slug).with_for_update())
    permitted = {s.mode: s for s in db.scalars(select(AnalyticsSource).where(AnalyticsSource.node == agent.slug))}
    if any(e.mode not in permitted for e in payload.events) or any(s.mode not in permitted for s in payload.sources):
        raise HTTPException(403, 'source not configured for agent')
    lower = now - timedelta(days=settings.analytics_session_days)
    for data in [*payload.events, *(s for r in payload.sources for s in r.sessions)]:
        if data.observed_at > now + timedelta(seconds=60) or data.started_at > now + timedelta(seconds=60):
            raise HTTPException(422, 'invalid accounting clock')
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
    agent.last_contact_at = now
    if agent.pruned_at is None or as_utc(agent.pruned_at) < now - timedelta(days=1):
        from .analytics_retention import prune
        prune(db, settings, now, [agent.slug])
        agent.pruned_at = now
    db.commit()


@router.post('/analytics-agents/{slug}/report', status_code=204)
def report(payload: AccountingReport, agent: AnalyticsAgent = Depends(authenticate_agent),
           db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    ingest(db, agent, payload, settings, utcnow())
