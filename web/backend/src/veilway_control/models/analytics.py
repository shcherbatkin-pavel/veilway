"""Analytics persistence models; registered by the compatibility package."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from .shared import utcnow


class AnalyticsAgent(Base):
    __tablename__ = "analytics_agents"
    __table_args__ = (CheckConstraint("slug IN ('aws-direct', 'yc-direct')", name="ck_analytics_agent_slug"),)
    slug: Mapped[str] = mapped_column(String(32), primary_key=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pruned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalyticsSource(Base):
    __tablename__ = "analytics_sources"
    __table_args__ = (CheckConstraint("mode IN ('aws-direct','yc-direct','yc-aws-multihop')", name="ck_analytics_source_mode"),)
    mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    node: Mapped[str | None] = mapped_column(ForeignKey("analytics_agents.slug"))
    first_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    configured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    epoch: Mapped[uuid.UUID | None] = mapped_column()
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    connection_count: Mapped[int | None] = mapped_column(Integer)
    capture_errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ProfileOwnership(Base):
    __tablename__ = "profile_ownership"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vpn_profiles.id"), nullable=False, index=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class VpnSession(Base):
    __tablename__ = "vpn_sessions"
    __table_args__ = (
        CheckConstraint("state IN ('open','closed','interrupted')", name="ck_vpn_session_state"),
        CheckConstraint("bytes_received >= 0 AND bytes_sent >= 0", name="ck_vpn_session_bytes"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    node: Mapped[str] = mapped_column(ForeignKey("analytics_agents.slug"), nullable=False)
    mode: Mapped[str] = mapped_column(ForeignKey("analytics_sources.mode"), nullable=False, index=True)
    epoch: Mapped[uuid.UUID] = mapped_column(nullable=False)
    certificate_serial: Mapped[str | None] = mapped_column(String(40))
    profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vpn_profiles.id"), index=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    reconnect: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recovered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    present: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    bytes_received: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    bytes_sent: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)


class AnalyticsReceipt(Base):
    __tablename__ = "analytics_receipts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    node: Mapped[str] = mapped_column(ForeignKey("analytics_agents.slug"), nullable=False)
    payload_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class ProfileUsage(Base):
    __tablename__ = "profile_usage"
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vpn_profiles.id"), primary_key=True)
    first_connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sessions: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    bytes_received: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    bytes_sent: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)


class AnalyticsOwner(Base):
    __tablename__ = "analytics_owners"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    first_connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DailyUsage(Base):
    __tablename__ = "analytics_daily_usage"
    __table_args__ = (UniqueConstraint("day", "mode", "profile_key", "owner_key", name="uq_analytics_daily_dimensions"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(ForeignKey("analytics_sources.mode"), nullable=False)
    profile_key: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_key: Mapped[str] = mapped_column(String(36), nullable=False)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vpn_profiles.id"))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    sessions_started: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reconnects: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sessions_closed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    interrupted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    bytes_received: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    bytes_sent: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)


class AnalyticsCoverage(Base):
    __tablename__ = "analytics_coverage"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    mode: Mapped[str] = mapped_column(ForeignKey("analytics_sources.mode"), primary_key=True)
    observations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
