"""Restart persistence models; registered by the compatibility package."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from .shared import utcnow

if TYPE_CHECKING:
    from .auth import User
    from .nodes import VpnVm


class RestartJob(Base):
    __tablename__ = "restart_jobs"
    __table_args__ = (
        CheckConstraint(
            "active_guard IS NULL OR active_guard = 1",
            name="ck_restart_job_active_guard",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    active_guard: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))

    user: Mapped[User] = relationship(back_populates="restart_jobs")
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
