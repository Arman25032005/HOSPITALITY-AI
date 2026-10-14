import re
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.db.session import get_db
from app.models import IntegrationConfig, Role, User
from app.services import audit
from app.services.access import get_hotel_for_user, require_role
from app.services.errors import ConflictError, NotFoundError, ValidationFailed

router = APIRouter(prefix="/api/v1/hotels/{hotel_id}/integrations", tags=["integrations"])
PROVIDERS = ("twilio_whatsapp", "telegram")


class IntegrationIn(BaseModel):
    enabled: bool
    config: dict = Field(default_factory=dict)


class IntegrationOut(BaseModel):
    id: uuid.UUID | None
    provider: str
    enabled: bool
    config: dict
    credentials_present: bool
    webhook_url: str | None
    status: str


def _creds(settings: Settings, provider: str) -> bool:
    if provider == "twilio_whatsapp":
        return bool(settings.twilio_account_sid and settings.twilio_auth_token
                    and settings.twilio_whatsapp_number)
    return bool(settings.telegram_bot_token and settings.telegram_webhook_secret)


def _out(settings: Settings, provider: str, row: IntegrationConfig | None) -> IntegrationOut:
    base = settings.public_base_url.rstrip("/")
    url = None
    if base and provider == "twilio_whatsapp":
        url = f"{base}/api/v1/webhooks/twilio/whatsapp"
    elif base and row:
        url = f"{base}/api/v1/webhooks/telegram/{row.id}"
    creds = _creds(settings, provider)
    # "configured" is the strongest claim made here: nothing verifies live delivery.
    status = ("not configured" if row is None or not row.enabled
              else "enabled, credentials missing" if not creds
              else "configured (live delivery not verified)")
    return IntegrationOut(id=row.id if row else None, provider=provider,
                          enabled=bool(row and row.enabled), config=row.config if row else {},
                          credentials_present=creds, webhook_url=url, status=status)


@router.get("", response_model=list[IntegrationOut])
def list_integrations(hotel_id: uuid.UUID, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, Role.admin, Role.manager)
    rows = {r.provider: r for r in db.scalars(select(IntegrationConfig).where(
        IntegrationConfig.hotel_id == hotel_id,
        IntegrationConfig.organization_id == user.organization_id))}
    return [_out(settings, p, rows.get(p)) for p in PROVIDERS]


@router.put("/{provider}", response_model=IntegrationOut)
def put_integration(hotel_id: uuid.UUID, provider: str, body: IntegrationIn,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db),
                    settings: Settings = Depends(get_settings)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, Role.admin)
    if provider not in PROVIDERS:
        raise NotFoundError("Unknown provider")
    config: dict = {}
    if provider == "twilio_whatsapp":
        number = str(body.config.get("number", ""))
        if body.enabled and not re.fullmatch(r"whatsapp:\+\d{6,15}", number):
            raise ValidationFailed("config.number must look like whatsapp:+14155238886")
        config = {"number": number} if number else {}
        if body.enabled:
            clash = db.scalar(select(IntegrationConfig.id).where(
                IntegrationConfig.provider == provider, IntegrationConfig.enabled.is_(True),
                IntegrationConfig.config["number"].astext == number,
                IntegrationConfig.hotel_id != hotel_id))
            if clash:
                raise ConflictError("That number is already assigned to another hotel")
    row = db.scalar(select(IntegrationConfig).where(
        IntegrationConfig.hotel_id == hotel_id, IntegrationConfig.provider == provider))
    if row is None:
        row = IntegrationConfig(organization_id=user.organization_id, hotel_id=hotel_id,
                                provider=provider)
        db.add(row)
    row.enabled, row.config = body.enabled, config
    db.flush()
    audit.record(db, org_id=user.organization_id, hotel_id=hotel_id,
                 action="integration.updated", actor_type="user", actor_id=str(user.id),
                 entity_type="integration", entity_id=str(row.id),
                 details={"provider": provider, "enabled": body.enabled})
    db.commit()
    return _out(settings, provider, row)
