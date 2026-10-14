"""Strict input contracts for configured accounting agents."""
from datetime import datetime
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .models import as_utc

Mode = Literal['aws-direct', 'yc-direct', 'yc-aws-multihop']
NODE_MODES = {'aws-direct': {'aws-direct'}, 'yc-direct': {'yc-direct', 'yc-aws-multihop'}}


class AccountingData(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: uuid.UUID
    epoch: uuid.UUID
    mode: Mode
    certificate_serial: str | None = Field(default=None, pattern=r'^[1-9A-F][0-9A-F]{0,39}$')
    profile_id: uuid.UUID | None = None
    started_at: datetime
    observed_at: datetime
    bytes_received: int = Field(ge=0, lt=2**63, strict=True)
    bytes_sent: int = Field(ge=0, lt=2**63, strict=True)

    @field_validator('started_at', 'observed_at')
    @classmethod
    def utc_timestamp(cls, value):
        if value.tzinfo is None or value.microsecond:
            raise ValueError('UTC timestamps with whole seconds required')
        return as_utc(value)

    @model_validator(mode='after')
    def chronology(self):
        if self.observed_at < self.started_at:
            raise ValueError('invalid session times')
        return self


class SessionEvent(AccountingData):
    id: uuid.UUID
    kind: Literal['start', 'end', 'interrupted']


class SourceReport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Mode
    status: Literal['available', 'unavailable']
    epoch: uuid.UUID | None = None
    observed_at: datetime | None = None
    connection_count: int | None = Field(default=None, ge=0, le=2000, strict=True)
    sessions: list[AccountingData] = Field(default_factory=list, max_length=2000)
    capture_errors: int = Field(default=0, ge=0, le=2**31-1, strict=True)

    @model_validator(mode='after')
    def shape(self):
        if self.status == 'available':
            if self.epoch is None or self.observed_at is None or self.connection_count is None:
                raise ValueError('incomplete snapshot')
            if self.observed_at.tzinfo is None or self.observed_at.microsecond:
                raise ValueError('UTC whole seconds required')
            self.observed_at = as_utc(self.observed_at)
            if len(self.sessions) > self.connection_count or len({s.session_id for s in self.sessions}) != len(self.sessions):
                raise ValueError('invalid snapshot count')
            if any(s.mode != self.mode or s.epoch != self.epoch or s.observed_at != self.observed_at for s in self.sessions):
                raise ValueError('mixed snapshot')
        elif self.sessions or self.connection_count is not None:
            raise ValueError('unavailable is not an empty snapshot')
        return self


class AccountingReport(BaseModel):
    model_config = ConfigDict(extra='forbid')
    events: list[SessionEvent] = Field(default_factory=list, max_length=500)
    sources: list[SourceReport] = Field(min_length=1, max_length=3)

    @model_validator(mode='after')
    def unique(self):
        if len({e.id for e in self.events}) != len(self.events) or len({s.mode for s in self.sources}) != len(self.sources):
            raise ValueError('duplicate event or source')
        return self
