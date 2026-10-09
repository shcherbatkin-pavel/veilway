from __future__ import annotations

import base64
import hmac
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import CrlAgent, CrlPublication, CrlSyncState, as_utc, utcnow
from .security import hash_token, require_admin

router = APIRouter()
ERROR_CODES = {"transport_unavailable", "invalid_bundle", "installation_failed"}


def agent_auth(slug: str, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    agent = db.get(CrlAgent, slug) if slug in {"aws-direct", "yc-direct"} else None
    if (agent is None or authorization is None or not authorization.startswith("Bearer ")
            or len(authorization) > 263 or not hmac.compare_digest(agent.token_hash, hash_token(authorization[7:]))):
        raise HTTPException(401, headers={"Cache-Control": "no-store"})
    return agent


def no_referrer(response):
    response.headers["Referrer-Policy"] = "no-referrer"


@router.get("/crl-agents/{slug}/bundle")
def bundle(response: Response, agent: CrlAgent = Depends(agent_auth), db: Session = Depends(get_db)):
    no_referrer(response)
    publication = db.scalar(select(CrlPublication).order_by(CrlPublication.version.desc()).limit(1))
    agent.last_contact_at = utcnow()
    db.commit()
    if publication is None or as_utc(publication.next_update) <= utcnow():
        raise HTTPException(503, "CRL unavailable", headers={"Cache-Control": "no-store"})
    return {"version": publication.version, "sha256": publication.sha256,
            "crl_base64": base64.b64encode(publication.pem).decode("ascii")}


class Receipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(strict=True, ge=1, lt=2**63)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.post("/crl-agents/{slug}/receipt", status_code=204)
def receipt(payload: Receipt, response: Response, agent: CrlAgent = Depends(agent_auth), db: Session = Depends(get_db)):
    no_referrer(response)
    agent = db.scalar(select(CrlAgent).where(CrlAgent.slug == agent.slug).with_for_update())
    publication = db.get(CrlPublication, payload.version)
    now = utcnow()
    if (publication is None or publication.sha256 != payload.sha256 or as_utc(publication.next_update) <= now
            or agent.acknowledged_version is not None and payload.version < agent.acknowledged_version):
        raise HTTPException(409, "receipt rejected", headers={"Cache-Control": "no-store"})
    agent.acknowledged_version, agent.acknowledged_sha256 = payload.version, payload.sha256
    agent.acknowledged_until, agent.last_contact_at = publication.next_update, now
    agent.error_code = None
    # Worker finalizes separately: keep the agent row lock out of the profile/job lock order.
    db.commit()


class DeliveryError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^(transport_unavailable|invalid_bundle|installation_failed)$")


@router.post("/crl-agents/{slug}/error", status_code=204)
def report_error(payload: DeliveryError, response: Response, agent: CrlAgent = Depends(agent_auth), db: Session = Depends(get_db)):
    no_referrer(response)
    agent.error_code, agent.last_contact_at = payload.code, utcnow()
    db.commit()


@router.get("/crl-delivery")
def delivery(response: Response, _=Depends(require_admin), db: Session = Depends(get_db)):
    no_referrer(response)
    latest = db.scalar(select(CrlPublication).order_by(CrlPublication.version.desc()).limit(1))
    state = db.get(CrlSyncState, 1)
    nodes = []
    now = utcnow()
    for slug in ("aws-direct", "yc-direct"):
        agent = db.get(CrlAgent, slug)
        status = "unconfigured"
        if agent is not None:
            status = "pending"
            if agent.acknowledged_until is not None and as_utc(agent.acknowledged_until) <= now:
                status = "expired"
            elif latest is not None and agent.acknowledged_version == latest.version:
                status = "current"
            if agent.last_contact_at is None or as_utc(agent.last_contact_at) < now - timedelta(minutes=2):
                status = "offline"
            if agent.error_code:
                status = "error"
        nodes.append({"slug": slug, "status": status,
                      "acknowledged_version": agent.acknowledged_version if agent else None,
                      "last_contact_at": agent.last_contact_at if agent else None,
                      "error_code": agent.error_code if agent else None})
    return {"version": latest.version if latest else None, "next_update": latest.next_update if latest else None,
            "publisher_error": state.error_code if state else "publication_unavailable", "nodes": nodes}
