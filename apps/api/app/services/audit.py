import uuid

from sqlalchemy.orm import Session

from app.models import AuditEvent


def record(
    db: Session,
    *,
    org_id: uuid.UUID,
    action: str,
    actor_type: str,
    actor_id: str | None = None,
    hotel_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict | None = None,
) -> None:
    db.add(AuditEvent(
        organization_id=org_id, hotel_id=hotel_id, actor_type=actor_type, actor_id=actor_id,
        action=action, entity_type=entity_type, entity_id=entity_id, details=details or {},
    ))
