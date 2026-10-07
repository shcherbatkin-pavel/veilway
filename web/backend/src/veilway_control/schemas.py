from __future__ import annotations

import uuid
from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


VmSlug = Literal["aws-direct", "yc-direct"]


class SessionResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    role: Literal["ADMIN", "USER"]
    csrf_token: str


class HeartbeatRequest(BaseModel):
    boot_id: uuid.UUID
    uptime_seconds: int = Field(ge=0)
    containers: dict[
        str, Literal["healthy", "starting", "unhealthy", "missing"]
    ] = Field(min_length=1, max_length=8)

    @field_validator("containers")
    @classmethod
    def validate_container_names(
        cls,
        values: dict[str, str],
    ) -> dict[str, str]:
        if any(not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", name) for name in values):
            raise ValueError("invalid container name")
        return values

    @property
    def healthy(self) -> bool:
        return all(state == "healthy" for state in self.containers.values())


class VmResponse(BaseModel):
    slug: VmSlug
    provider: Literal["aws", "yandex"]
    state: Literal["healthy", "degraded", "unknown", "restarting"]
    last_heartbeat_at: datetime | None


class RestartCreateRequest(BaseModel):
    targets: list[VmSlug] = Field(min_length=1, max_length=2)

    @field_validator("targets")
    @classmethod
    def unique_targets(cls, values: list[VmSlug]) -> list[VmSlug]:
        if len(set(values)) != len(values):
            raise ValueError("targets must be unique")
        return values


class RestartTargetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: VmSlug
    position: int
    status: str
    dispatched_at: datetime | None
    recovered_at: datetime | None
    error_code: str | None


class RestartJobResponse(BaseModel):
    id: uuid.UUID
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    targets: list[RestartTargetResponse]


ProfileMode = Literal["yc-direct", "aws-direct", "yc-aws-multihop"]
ProfileStatus = Literal["issuing", "active", "expired", "revoking", "revoked", "failed"]


class ProfileCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: uuid.UUID
    device_name: str = Field(min_length=1, max_length=128)
    mode: ProfileMode
    owner_id: uuid.UUID | None = None
    duration_days: int | None = Field(default=None, ge=1, strict=True)
    expires_at: datetime | None = None

    @field_validator("device_name")
    @classmethod
    def clean_device_name(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("invalid device name")
        value = value.strip()
        if not value:
            raise ValueError("invalid device name")
        return value

    @field_validator("expires_at")
    @classmethod
    def timezone_required(cls, value: datetime | None):
        if value is not None and (value.tzinfo is None or value.microsecond != 0):
            raise ValueError("expiry requires timezone and whole seconds")
        return value


class ProfileRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_name: str = Field(min_length=1, max_length=128)

    _clean_name = field_validator("device_name")(ProfileCreateRequest.clean_device_name.__func__)


class ProfileAssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner_id: uuid.UUID


class ProfileRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: uuid.UUID


class ProfileResponse(BaseModel):
    id: uuid.UUID
    device_name: str
    mode: ProfileMode
    owner_id: uuid.UUID | None
    status: ProfileStatus
    created_at: datetime
    expires_at: datetime


class ProfileJobResponse(BaseModel):
    id: uuid.UUID
    profile_id: uuid.UUID
    kind: Literal["issue", "revoke"]
    status: Literal["queued", "running", "succeeded", "failed", "needs_review"]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None


class ProfileCreateResponse(BaseModel):
    profile: ProfileResponse
    job: ProfileJobResponse


class RegisteredUserResponse(BaseModel):
    id: uuid.UUID
    email: str


class ProfileAuditResponse(BaseModel):
    id: uuid.UUID
    actor_id: uuid.UUID
    action: str
    object_id: uuid.UUID
    result: str
    created_at: datetime
