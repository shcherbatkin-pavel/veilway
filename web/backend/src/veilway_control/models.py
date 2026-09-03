from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Normalize timestamps returned by lightweight test databases."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


ALLOWED_VM_SLUGS = ("aws-direct", "yc-direct")
EXPECTED_VM_CONFIGURATION = {
    "aws-direct": ("aws", 1),
    "yc-direct": ("yandex", 2),
}


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    login: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    sessions: Mapped[list[AdminSession]] = relationship(
        back_populates="admin", cascade="all, delete-orphan"
    )
    restart_jobs: Mapped[list[RestartJob]] = relationship(back_populates="admin")


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    admin_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admins.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    csrf_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    admin: Mapped[Admin] = relationship(back_populates="sessions")


class VpnVm(Base):
    __tablename__ = "vpn_vms"
    __table_args__ = (
        UniqueConstraint("provider", "instance_id", name="uq_vpn_vm_provider_instance"),
        CheckConstraint(
            "(slug = 'aws-direct' AND provider = 'aws' AND restart_order = 1) OR "
            "(slug = 'yc-direct' AND provider = 'yandex' AND restart_order = 2)",
            name="ck_vpn_vm_exact_allowlist",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    instance_id: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    restart_order: Mapped[int] = mapped_column(Integer, nullable=False)
    heartbeat_token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    heartbeat: Mapped[VmHeartbeat | None] = relationship(
        back_populates="vm", uselist=False, cascade="all, delete-orphan"
    )
    restart_targets: Mapped[list[RestartTarget]] = relationship(back_populates="vm")


class VmHeartbeat(Base):
    __tablename__ = "vm_heartbeats"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    vm_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vpn_vms.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    boot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    healthy: Mapped[bool] = mapped_column(Boolean, nullable=False)
    uptime_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    vm: Mapped[VpnVm] = relationship(back_populates="heartbeat")


class RestartJob(Base):
    __tablename__ = "restart_jobs"
    __table_args__ = (
        CheckConstraint(
            "active_guard IS NULL OR active_guard = 1",
            name="ck_restart_job_active_guard",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    admin_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admins.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    active_guard: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))

    admin: Mapped[Admin] = relationship(back_populates="restart_jobs")
    targets: Mapped[list[RestartTarget]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="RestartTarget.position",
    )


class RestartTarget(Base):
    __tablename__ = "restart_targets"
    __table_args__ = (
        UniqueConstraint("job_id", "vm_id", name="uq_restart_target_job_vm"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("restart_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vm_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vpn_vms.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    previous_boot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    cloud_request_id: Mapped[str | None] = mapped_column(String(160))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))

    job: Mapped[RestartJob] = relationship(back_populates="targets")
    vm: Mapped[VpnVm] = relationship(back_populates="restart_targets")


Index(
    "uq_restart_jobs_one_active",
    RestartJob.active_guard,
    unique=True,
    postgresql_where=RestartJob.active_guard.is_not(None),
)
