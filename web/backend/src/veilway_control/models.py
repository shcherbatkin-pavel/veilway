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
    JSON,
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


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('ADMIN', 'USER')", name="ck_user_role"),
        CheckConstraint(
            "(google_sub IS NOT NULL AND email IS NOT NULL AND "
            "login IS NULL AND password_hash IS NULL) OR "
            "(google_sub IS NULL AND email IS NULL AND "
            "login IS NOT NULL AND password_hash IS NOT NULL)",
            name="ck_user_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Legacy credentials remain independent of Google identities until stage 2.
    login: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="USER", server_default="USER")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    restart_jobs: Mapped[list[RestartJob]] = relationship(back_populates="user")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
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

    user: Mapped[User] = relationship(back_populates="sessions")


class OAuthLoginAttempt(Base):
    __tablename__ = "oauth_login_attempts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    state_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    browser_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    nonce_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class GoogleAdminBinding(Base):
    """A locked singleton pins the administrator to a user, never just an email."""

    __tablename__ = "google_admin_binding"
    __table_args__ = (CheckConstraint("id = 1", name="ck_google_admin_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, unique=True
    )


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
