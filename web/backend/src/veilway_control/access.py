"""Role and ownership rules shared by present and future API handlers."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from .models import ProfileJob, User, VpnProfile


def ensure_active_user(user: User) -> None:
    if not user.is_active:
        raise HTTPException(status_code=401)
    if user.role not in {"ADMIN", "USER"}:
        raise HTTPException(status_code=403)


def ensure_admin(user: User) -> None:
    ensure_active_user(user)
    if user.role != "ADMIN":
        raise HTTPException(status_code=403)


def registered_users() -> Select[tuple[User]]:
    """Eligible profile owners: active Google-registered USER accounts."""
    return select(User).where(
        User.role == "USER", User.is_active.is_(True), User.google_sub.is_not(None),
    )


def visible_profiles(user: User) -> Select[tuple[VpnProfile]]:
    """Filter in SQL so unauthorized metadata never enters an API response."""
    ensure_active_user(user)
    query = select(VpnProfile)
    if user.role == "USER":
        query = query.where(VpnProfile.owner_id == user.id)
    return query


def visible_jobs(user: User) -> Select[tuple[ProfileJob]]:
    """Apply the same active-user and ownership rules as profile metadata."""
    ensure_active_user(user)
    query = select(ProfileJob).join(VpnProfile, ProfileJob.profile_id == VpnProfile.id)
    if user.role == "USER":
        query = query.where(VpnProfile.owner_id == user.id)
    return query


def get_visible_profile(db: Session, user: User, profile_id: uuid.UUID) -> VpnProfile:
    profile = db.scalar(visible_profiles(user).where(VpnProfile.id == profile_id))
    if profile is None:
        raise HTTPException(status_code=404)
    return profile
