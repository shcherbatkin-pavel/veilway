"""Response contracts for administrator analytics."""
from datetime import date, datetime
from typing import Literal
import uuid

from pydantic import BaseModel
from .accounting_schemas import Mode


class SourceStatus(BaseModel):
    mode: Mode
    state: Literal['unconfigured', 'unavailable', 'stale', 'fresh']
    configured_at: datetime | None
    collection_started_at: datetime | None
    observed_at: datetime | None
    age_seconds: int | None
    connections: int | None
    capture_errors: int


class OnlineStats(BaseModel):
    complete: bool
    connections: int | None
    profiles: int | None
    users: int | None
    confirmed_connections: int
    confirmed_profiles: int
    confirmed_users: int
    unmatched_connections: int
    unassigned_profiles: int


class ActivityStats(BaseModel):
    users: int
    profiles: int
    dau: int
    wau: int
    mau: int
    new_users: int
    returning_users: int
    sessions_started: int
    sessions_closed: int
    interruptions: int
    reconnects: int
    average_duration_seconds: float | None
    bytes_received: int
    bytes_sent: int


class Funnel(BaseModel):
    issued: int
    download_recorded: int
    connected: int
    mature_7d: int
    connected_within_7d: int
    pending_7d: int
    activation_7d_percent: float | None
    average_first_connection_seconds: float | None


class Lifecycle(BaseModel):
    valid_profiles: int
    never_observed: int
    inactive_30d: int
    used_expiring_7d: int


class DayStats(BaseModel):
    day: date
    observed: bool = False
    users: int
    profiles: int
    sessions_started: int
    bytes_received: int
    bytes_sent: int


class ModeStats(BaseModel):
    mode: Mode
    users: int
    profiles: int
    sessions_started: int
    bytes_received: int
    bytes_sent: int
    connections: int | None


class BusinessOverview(BaseModel):
    generated_at: datetime
    period_days: int
    timezone: Literal['UTC'] = 'UTC'
    detail_retention_days: int
    aggregate_retention_days: int
    collection_started_at: datetime | None
    sources: list[SourceStatus]
    online: OnlineStats
    activity: ActivityStats
    funnel: Funnel
    lifecycle: Lifecycle
    daily: list[DayStats]
    modes: list[ModeStats]


class ProfileAnalytics(BaseModel):
    profile_id: uuid.UUID
    device_name: str
    mode: Mode
    owner_email: str | None
    status: str
    expires_at: datetime
    online: bool | None
    first_connected_at: datetime | None
    last_connected_at: datetime | None
    last_seen_at: datetime | None
    sessions_started: int
    bytes_received: int
    bytes_sent: int


class SessionAnalytics(BaseModel):
    session_id: uuid.UUID
    profile_id: uuid.UUID | None
    device_name: str | None
    owner_email: str | None
    mode: Mode
    state: Literal['open', 'closed', 'interrupted']
    online: bool | None
    started_at: datetime
    last_seen_at: datetime
    ended_at: datetime | None
    duration_seconds: int | None
    bytes_received: int
    bytes_sent: int
    recovered: bool
    reconnect: bool


class ProfilePage(BaseModel):
    items: list[ProfileAnalytics]
    total: int


class SessionPage(BaseModel):
    items: list[SessionAnalytics]
    total: int
    retained_since: datetime
