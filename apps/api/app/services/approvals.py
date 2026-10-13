import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApprovalRequest, Role, User
from app.services import audit
from app.services.errors import ConflictError, ForbiddenError, NotFoundError

DECIDERS = (Role.admin, Role.manager, Role.receptionist)


def request_approval(db: Session, *, org_id, hotel_id, kind: str, payload: dict,
                     guest_id: uuid.UUID | None, dedupe_key: str | None = None,
                     actor_type: str = "agent", actor_id: str | None = None) -> tuple[ApprovalRequest, bool]:
    """Create a pending approval. With `dedupe_key`, an existing pending request for the same key
    is returned instead (second element False)."""
    if dedupe_key:
        existing = db.scalar(select(ApprovalRequest).where(
            ApprovalRequest.hotel_id == hotel_id, ApprovalRequest.kind == kind,
            ApprovalRequest.status == "pending",
            ApprovalRequest.payload["dedupe_key"].astext == dedupe_key))
        if existing:
            return existing, False
        payload = {**payload, "dedupe_key": dedupe_key}
    ap = ApprovalRequest(organization_id=org_id, hotel_id=hotel_id, kind=kind, payload=payload,
                         requested_by_guest_id=guest_id)
    db.add(ap)
    db.flush()
    audit.record(db, org_id=org_id, hotel_id=hotel_id, action="approval.requested",
                 actor_type=actor_type, actor_id=actor_id, entity_type="approval_request",
                 entity_id=str(ap.id), details={"kind": kind})
    db.commit()
    return ap, True


def list_approvals(db: Session, *, user: User, hotel_id, status: str | None = None,
                   limit: int = 100) -> list[ApprovalRequest]:
    if user.role not in DECIDERS:
        raise ForbiddenError("Insufficient permissions")
    q = select(ApprovalRequest).where(ApprovalRequest.organization_id == user.organization_id,
                                      ApprovalRequest.hotel_id == hotel_id)
    if status:
        q = q.where(ApprovalRequest.status == status)
    return list(db.scalars(q.order_by(ApprovalRequest.created_at.desc()).limit(min(limit, 200))))


def decide(db: Session, *, user: User, hotel_id, approval_id: uuid.UUID, approve: bool,
           note: str | None = None, approved_until: str | None = None) -> ApprovalRequest:
    if user.role not in DECIDERS:
        raise ForbiddenError("Insufficient permissions")
    ap = db.scalar(select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
                   .with_for_update())
    if ap is None or ap.organization_id != user.organization_id or ap.hotel_id != hotel_id:
        raise NotFoundError("Approval not found")
    if ap.status != "pending":
        raise ConflictError("This request has already been decided")
    ap.status = "approved" if approve else "denied"
    ap.decided_by, ap.decided_at = user.id, datetime.now(UTC)
    ap.payload = {**ap.payload, "decision": {"note": (note or "")[:500],
                                              "approved_until": approved_until if approve else None}}
    audit.record(db, org_id=ap.organization_id, hotel_id=hotel_id, action=f"approval.{ap.status}",
                 actor_type="user", actor_id=str(user.id), entity_type="approval_request",
                 entity_id=str(ap.id), details={"kind": ap.kind})
    db.commit()
    return ap
