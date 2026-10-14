"""Crl persistence models; registered by the compatibility package."""
from __future__ import annotations
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from .shared import utcnow


class CrlPublication(Base):
    __tablename__ = "crl_publications"
    version: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    ca_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    pem: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    this_update: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_update: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class CrlAgent(Base):
    __tablename__ = "crl_agents"
    __table_args__ = (CheckConstraint("slug IN ('aws-direct', 'yc-direct')", name="ck_crl_agent_slug"),)
    slug: Mapped[str] = mapped_column(String(32), primary_key=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_version: Mapped[int | None] = mapped_column(BigInteger)
    acknowledged_sha256: Mapped[str | None] = mapped_column(String(64))
    acknowledged_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(32))


class CrlSyncState(Base):
    __tablename__ = "crl_sync_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_crl_sync_singleton"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(32))
