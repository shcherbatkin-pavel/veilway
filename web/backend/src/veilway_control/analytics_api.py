"""Administrator analytics HTTP routes."""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .accounting_schemas import Mode
from .analytics_schemas import BusinessOverview, ProfilePage, SessionPage
from .analytics_queries import build_business_overview, build_profile_page, build_session_page
from .config import Settings, get_settings
from .database import get_db
from .models import utcnow
from .security import require_admin

router = APIRouter()


@router.get('/analytics/overview', response_model=BusinessOverview)
def overview(_=Depends(require_admin), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
             days: int = Query(default=30, ge=1, le=365), mode: Mode | None = None):
    return build_business_overview(db, settings, utcnow(), days, mode)


@router.get('/analytics/profiles', response_model=ProfilePage)
def profiles(_=Depends(require_admin), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
             days: int = Query(default=30, ge=1, le=365), mode: Mode | None = None,
             online_only: bool = False, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return build_profile_page(db, settings, utcnow(), days, mode, online_only, limit, offset)


@router.get('/analytics/sessions', response_model=SessionPage)
def sessions(_=Depends(require_admin), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
             days: int = Query(default=30, ge=1, le=365), mode: Mode | None = None, profile_id: uuid.UUID | None = None,
             limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    return build_session_page(db, settings, utcnow(), days, mode, profile_id, limit, offset)
