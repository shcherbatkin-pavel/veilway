"""Authenticated HTTP ingestion of configured accounting reports."""
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .accounting_schemas import AccountingReport, Mode, NODE_MODES
from .accounting import ingest
from .config import Settings, get_settings
from .database import get_db
from .models import AnalyticsAgent, utcnow
from .security import hash_token

router = APIRouter()


def authenticate_agent(slug: str, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    if slug not in NODE_MODES or not authorization or not authorization.startswith('Bearer ') or len(authorization) > 263:
        raise HTTPException(401)
    agent = db.get(AnalyticsAgent, slug)
    if agent is None or not hmac.compare_digest(agent.token_hash, hash_token(authorization[7:])):
        raise HTTPException(401)
    return agent


@router.post('/analytics-agents/{slug}/report', status_code=204)
def report(payload: AccountingReport, agent: AnalyticsAgent = Depends(authenticate_agent),
           db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    ingest(db, agent, payload, settings, utcnow())
