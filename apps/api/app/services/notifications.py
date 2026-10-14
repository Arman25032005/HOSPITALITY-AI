import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import Notification, NotificationRead, Role, User
from app.services.access import user_department_ids

FRONT_OFFICE = [Role.admin.value, Role.manager.value, Role.receptionist.value]


def notify(db: Session, *, org_id, hotel_id, kind: str, title: str, severity: str = "info",
           entity_type: str | None = None, entity_id: str | None = None,
           department_id: uuid.UUID | None = None, roles: list[str] | None = None) -> Notification:
    """Queue a staff notification (caller commits). Titles carry references, never guest text."""
    n = Notification(organization_id=org_id, hotel_id=hotel_id, kind=kind, title=title[:200],
                     severity=severity, entity_type=entity_type, entity_id=entity_id,
                     department_id=department_id, roles=roles or FRONT_OFFICE)
    db.add(n)
    return n


def list_for_user(db: Session, *, user: User, hotel_id, unread_only: bool = False,
                  limit: int = 50) -> list[tuple[Notification, bool]]:
    visible = Notification.roles.contains([user.role.value])
    if user.role == Role.department_staff:
        depts = user_department_ids(db, user, hotel_id)
        visible = and_(Notification.department_id.in_(depts)) if depts else Notification.id.is_(None)
    q = (select(Notification, NotificationRead.id)
         .outerjoin(NotificationRead, and_(NotificationRead.notification_id == Notification.id,
                                           NotificationRead.user_id == user.id))
         .where(Notification.organization_id == user.organization_id,
                Notification.hotel_id == hotel_id, or_(visible, False)))
    if unread_only:
        q = q.where(NotificationRead.id.is_(None))
    rows = db.execute(q.order_by(Notification.created_at.desc()).limit(min(limit, 200))).all()
    return [(n, rid is not None) for n, rid in rows]


def mark_read(db: Session, *, user: User, hotel_id, notification_id) -> bool:
    visible = {n.id for n, _ in list_for_user(db, user=user, hotel_id=hotel_id, limit=200)}
    if notification_id not in visible:
        return False
    if not db.scalar(select(NotificationRead.id).where(
            NotificationRead.notification_id == notification_id,
            NotificationRead.user_id == user.id)):
        db.add(NotificationRead(notification_id=notification_id, user_id=user.id))
        db.commit()
    return True
