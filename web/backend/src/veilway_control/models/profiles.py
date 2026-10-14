"""Profiles persistence models; registered by the compatibility package."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base
from .shared import utcnow

if TYPE_CHECKING:
    from .auth import User


class VpnProfile(Base):
    """Metadata only: private material belongs to the separate PKI service."""

    __tablename__ = "vpn_profiles"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('yc-direct', 'aws-direct', 'yc-aws-multihop')",
            name="ck_vpn_profile_mode",
        ),
        CheckConstraint(
            "status IN ('issuing', 'active', 'expired', 'revoking', 'revoked', 'failed')",
            name="ck_vpn_profile_status",
        ),
        CheckConstraint("expires_at > created_at", name="ck_vpn_profile_expiry"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    device_name: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="issuing", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    owner: Mapped[User | None] = relationship(foreign_keys=[owner_id])
    certificate_serial: Mapped[str | None] = mapped_column(String(40))
    certificate_sha256: Mapped[str | None] = mapped_column(String(64))
    legacy_import_sha256: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    jobs: Mapped[list[ProfileJob]] = relationship(back_populates="profile")


class ProfileJob(Base):
    __tablename__ = "profile_jobs"
    __table_args__ = (
        UniqueConstraint("profile_id", "kind", name="uq_profile_job_kind"),
        CheckConstraint("kind IN ('issue', 'revoke')", name="ck_profile_job_kind"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'needs_review')",
            name="ck_profile_job_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vpn_profiles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    requested_by_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        default=uuid.uuid4, unique=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    request_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    claim_token: Mapped[uuid.UUID | None] = mapped_column()
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    crl_number: Mapped[int | None] = mapped_column(BigInteger)

    profile: Mapped[VpnProfile] = relationship(back_populates="jobs")
    requested_by: Mapped[User] = relationship()


class ProfileAuditEvent(Base):
    __tablename__ = "profile_audit_events"
    __table_args__ = (
        CheckConstraint("action IN ('create', 'rename', 'assign', 'download', 'revoke', 'issue_result', 'revoke_result', 'import')", name="ck_profile_audit_action"),
        CheckConstraint("result IN ('accepted', 'succeeded', 'denied', 'unavailable', 'failed', 'needs_review', 'local_revocation_applied')", name="ck_profile_audit_result"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
