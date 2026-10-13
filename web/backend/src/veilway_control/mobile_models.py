"""Device authorization metadata; never stores profiles or plaintext tokens."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import utcnow


class MobileDeviceAttempt(Base):
    __tablename__ = "mobile_device_attempts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    device_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    code_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    next_poll_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    poll_seconds: Mapped[int] = mapped_column(Integer, default=5)


class MobileSession(Base):
    __tablename__ = "mobile_sessions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class MobileRateBucket(Base):
    __tablename__ = "mobile_rate_buckets"
    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    window: Mapped[int] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer)
