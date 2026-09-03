from __future__ import annotations

import uuid
from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


VmSlug = Literal["aws-direct", "yc-direct"]


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class SessionResponse(BaseModel):
    login: str
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
