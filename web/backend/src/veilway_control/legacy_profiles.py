"""Recoverable operator synchronization of PKI-verified legacy metadata only."""
from datetime import datetime
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select

from .models import GoogleAdminBinding, User, VpnProfile, as_utc
from .pki import PkiUnavailable
from .profiles import audit
from .schemas import ProfileRenameRequest


class LegacyRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: uuid.UUID
    mode: Literal["yc-direct", "aws-direct", "yc-aws-multihop"]
    device_name: str = Field(min_length=1, max_length=128)
    created_at: datetime
    expires_at: datetime
    status: Literal["active", "expired", "revoked"]
    serial: str = Field(pattern=r"^[0-9A-F]{1,40}$")
    certificate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    import_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("device_name")
    @classmethod
    def clean_name(cls, value):
        return ProfileRenameRequest(device_name=value).device_name

    @model_validator(mode="after")
    def valid_dates(self):
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None or self.expires_at <= self.created_at:
            raise ValueError("invalid dates")
        return self


class LegacyPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: uuid.UUID
    profiles: list[LegacyRecord] = Field(max_length=64)
    next_after: str

    @field_validator("next_after")
    @classmethod
    def canonical_cursor(cls, value):
        if value and str(uuid.UUID(value)) != value:
            raise ValueError("invalid cursor")
        return value


def synchronize_legacy_profiles(session_factory, client, admin_id, *, fault=None):
    """PKI commits first. A retry adopts the same UUID and original provenance.

    Entire DB batch is atomic; no signing jobs are created. Failures leave PKI
    profiles available for another sync and leave existing DB owners untouched.
    """
    fault = fault or (lambda point: None)
    with session_factory() as db:
        actor = db.scalar(select(User).where(User.id == admin_id, User.role == "ADMIN",
            User.is_active.is_(True), User.google_sub.is_not(None)).with_for_update())
        binding = db.get(GoogleAdminBinding, 1)
        if actor is None or binding is None or binding.user_id != actor.id:
            raise PkiUnavailable("invalid_request")
        records, generation, cursor, seen = [], "", "", set()
        for _ in range(1000):
            page = LegacyPage.model_validate(client.legacy_catalog(cursor, generation))
            if generation and str(page.generation) != generation:
                raise PkiUnavailable("conflict")
            generation = str(page.generation)
            for record in page.profiles:
                if record.profile_id in seen or str(record.profile_id) <= cursor:
                    raise PkiUnavailable("conflict")
                seen.add(record.profile_id)
                records.append(record)
            if not page.next_after:
                break
            if page.next_after <= cursor or any(str(record.profile_id) > page.next_after for record in page.profiles):
                raise PkiUnavailable("conflict")
            cursor = page.next_after
        else:
            raise PkiUnavailable("conflict")
        fault("after_catalog")
        for record in records:
            profile = db.scalar(select(VpnProfile).where(VpnProfile.id == record.profile_id).with_for_update())
            duplicates = db.scalar(select(VpnProfile.id).where(VpnProfile.id != record.profile_id,
                (VpnProfile.certificate_serial == record.serial) | (VpnProfile.certificate_sha256 == record.certificate_sha256)).limit(1))
            if duplicates:
                raise PkiUnavailable("conflict")
            if profile is not None:
                if (profile.legacy_import_sha256 != record.import_sha256 or profile.mode != record.mode
                    or profile.certificate_serial != record.serial or profile.certificate_sha256 != record.certificate_sha256
                    or as_utc(profile.created_at) != record.created_at or as_utc(profile.expires_at) != record.expires_at):
                    raise PkiUnavailable("conflict")
                # Replay never changes device name, owner or revocation delivery state.
                continue
            profile = VpnProfile(id=record.profile_id, device_name=record.device_name, mode=record.mode,
                created_at=record.created_at, expires_at=record.expires_at, status=record.status,
                created_by_id=actor.id, owner_id=None, certificate_serial=record.serial,
                certificate_sha256=record.certificate_sha256, legacy_import_sha256=record.import_sha256)
            db.add(profile)
            audit(db, actor.id, "import", record.profile_id, "succeeded")
            db.flush()
        fault("before_db_commit")
        # Fail closed if PKI changed while assembling this database batch.
        client.legacy_catalog("", generation)
        db.commit()
        return len(records)
