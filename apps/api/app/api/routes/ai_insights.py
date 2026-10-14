import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.db.session import get_db
from app.models import User
from app.services import ai_metrics as m
from app.services.access import get_hotel_for_user

router = APIRouter(prefix="/api/v1/hotels/{hotel_id}", tags=["ai-insights"])


def _ctx(db, user, hotel_id):
    get_hotel_for_user(db, user, hotel_id)


@router.get("/ai/kpis")
def kpis(hotel_id: uuid.UUID, days: int = Query(7, ge=1, le=90),
         user: User = Depends(get_current_user), db: Session = Depends(get_db),
         settings: Settings = Depends(get_settings)):
    _ctx(db, user, hotel_id)
    return m.kpis(db, user=user, hotel_id=hotel_id, settings=settings, days=days)


@router.get("/ai/executions")
def executions(hotel_id: uuid.UUID, days: int = Query(7, ge=1, le=90), outcome: str | None = None,
               limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               user: User = Depends(get_current_user), db: Session = Depends(get_db),
               settings: Settings = Depends(get_settings)):
    _ctx(db, user, hotel_id)
    return m.executions(db, user=user, hotel_id=hotel_id, settings=settings, days=days,
                        outcome=outcome, limit=limit, offset=offset)


@router.get("/ai/tools")
def tools(hotel_id: uuid.UUID, days: int = Query(7, ge=1, le=90),
          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ctx(db, user, hotel_id)
    return m.tool_stats(db, user=user, hotel_id=hotel_id, days=days)


@router.get("/ai/unanswered")
def unanswered(hotel_id: uuid.UUID, days: int = Query(7, ge=1, le=90),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ctx(db, user, hotel_id)
    return m.unanswered(db, user=user, hotel_id=hotel_id, days=days)


@router.get("/ai/flagged")
def flagged(hotel_id: uuid.UUID, days: int = Query(30, ge=1, le=90),
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ctx(db, user, hotel_id)
    return m.flagged(db, user=user, hotel_id=hotel_id, days=days)


@router.get("/audit")
def audit(hotel_id: uuid.UUID, action: str | None = None, entity_type: str | None = None,
          limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ctx(db, user, hotel_id)
    return m.audit_log(db, user=user, hotel_id=hotel_id, action=action, entity_type=entity_type,
                       limit=limit, offset=offset)
