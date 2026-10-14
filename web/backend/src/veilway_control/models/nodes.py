"""Nodes persistence models; registered by the compatibility package."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from .shared import utcnow

if TYPE_CHECKING:
    from .restart import RestartTarget


ALLOWED_VM_SLUGS = ("aws-direct", "yc-direct")
EXPECTED_VM_CONFIGURATION = {
    "aws-direct": ("aws", 1),
    "yc-direct": ("yandex", 2),
}


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
    containers: Mapped[dict[str, str] | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    boot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    healthy: Mapped[bool] = mapped_column(Boolean, nullable=False)
    uptime_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    vm: Mapped[VpnVm] = relationship(back_populates="heartbeat")
