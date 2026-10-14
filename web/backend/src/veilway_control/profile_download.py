"""Shared profile download policy for browser and owner-only mobile channels."""
from __future__ import annotations
import uuid
from fastapi import Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from .config import Settings, get_settings
from .models import User, VpnProfile, as_utc, utcnow
from .pki import PkiClient, PkiUnavailable
from .profiles import audit, locked_profile


def get_pki_client(settings: Settings = Depends(get_settings)):
    return PkiClient(settings.pki_socket_path)


def download_response(profile_id: uuid.UUID, user: User, db: Session, client: PkiClient, *, owner_only: bool = False):
    if owner_only:
        profile = db.scalar(select(VpnProfile).where(VpnProfile.id == profile_id, VpnProfile.owner_id == user.id).with_for_update())
        if profile is None:
            raise HTTPException(404, headers={"Cache-Control": "no-store"})
    else:
        profile = locked_profile(db, user, profile_id)
    if profile.status != "active" or as_utc(profile.expires_at) <= utcnow():
        audit(db, user.id, "download", profile.id, "denied")
        db.commit()
        raise HTTPException(409, "profile is not active", headers={"Cache-Control": "no-store"})
    try:
        material = client.download(profile.id)
    except PkiUnavailable:
        audit(db, user.id, "download", profile.id, "unavailable")
        db.commit()
        raise HTTPException(503, "profile download unavailable", headers={"Cache-Control": "no-store"}) from None
    audit(db, user.id, "download", profile.id, "succeeded")
    db.commit()
    return Response(material, media_type="application/x-openvpn-profile", headers={
        "Content-Disposition": f'attachment; filename="veilway-{profile.id}.ovpn"',
        "Cache-Control": "no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    })
