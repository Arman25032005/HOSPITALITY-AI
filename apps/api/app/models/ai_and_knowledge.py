import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
    func, literal_column, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


def _fk(table: str, **kw):
    return mapped_column(UUID(as_uuid=True), ForeignKey(f"{table}.id"), **kw)


class Conversation(Base, IdMixin, TimestampMixin):
    __tablename__ = "conversations"
    organization_id: Mapped[uuid.UUID] = _fk("organizations", index=True, nullable=False)
    hotel_id: Mapped[uuid.UUID] = _fk("hotels", index=True, nullable=False)
    guest_id: Mapped[uuid.UUID | None] = _fk("guests", index=True)
    staff_user_id: Mapped[uuid.UUID | None] = _fk("users")
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    state: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    retention_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base, IdMixin, TimestampMixin):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_id", "external_message_id"),
                      Index("ix_messages_conv_created", "conversation_id", "created_at"))
    organization_id: Mapped[uuid.UUID] = _fk("organizations", index=True, nullable=False)
    conversation_id: Mapped[uuid.UUID] = _fk("conversations", index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user|assistant|system|staff
    content: Mapped[str] = mapped_column(Text, nullable=False)
    external_message_id: Mapped[str | None] = mapped_column(String(128))  # webhook idempotency
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class KnowledgeDocument(Base, IdMixin, TimestampMixin):
    __tablename__ = "knowledge_documents"
    __table_args__ = (UniqueConstraint("hotel_id", "doc_key", "version"),)
    organization_id: Mapped[uuid.UUID] = _fk("organizations", index=True, nullable=False)
    hotel_id: Mapped[uuid.UUID] = _fk("hotels", index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)
    language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    effective_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_confidential: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str | None] = mapped_column(String(500))
    doc_key: Mapped[str] = mapped_column(String(200), nullable=False)  # stable identity across versions
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    content_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False)


class DocumentChunk(Base, IdMixin, TimestampMixin):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index"),
        Index("ix_document_chunks_fts", func.to_tsvector(literal_column("'simple'"), text("search_tokens")),
              postgresql_using="gin"),
    )
    organization_id: Mapped[uuid.UUID] = _fk("organizations", index=True, nullable=False)
    hotel_id: Mapped[uuid.UUID] = _fk("hotels", index=True, nullable=False)
    document_id: Mapped[uuid.UUID] = _fk("knowledge_documents", index=True, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(ARRAY(Float))
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    suspicious: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    search_tokens: Mapped[str] = mapped_column(Text, default="", nullable=False)  # stemmed terms


class IngestionJob(Base, IdMixin, TimestampMixin):
    __tablename__ = "ingestion_jobs"
    organization_id: Mapped[uuid.UUID] = _fk("organizations", index=True, nullable=False)
    hotel_id: Mapped[uuid.UUID] = _fk("hotels", index=True, nullable=False)
    document_id: Mapped[uuid.UUID] = _fk("knowledge_documents", index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class AgentExecution(Base, IdMixin, TimestampMixin):
    __tablename__ = "agent_executions"
    __table_args__ = (Index("ix_agent_exec_hotel_created", "hotel_id", "created_at"),)
    organization_id: Mapped[uuid.UUID] = _fk("organizations", index=True, nullable=False)
    hotel_id: Mapped[uuid.UUID] = _fk("hotels", index=True, nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = _fk("conversations", index=True)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(50))
    outcome: Mapped[str | None] = mapped_column(String(30))
    steps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    llm_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    trace: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)  # sanitized


class ToolExecution(Base, IdMixin, TimestampMixin):
    __tablename__ = "tool_executions"
    organization_id: Mapped[uuid.UUID] = _fk("organizations", index=True, nullable=False)
    agent_execution_id: Mapped[uuid.UUID] = _fk("agent_executions", index=True, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    arguments: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)  # redacted
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class AuditEvent(Base, IdMixin):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),
                      Index("ix_audit_hotel_created", "hotel_id", "created_at"))
    organization_id: Mapped[uuid.UUID] = _fk("organizations", index=True, nullable=False)
    hotel_id: Mapped[uuid.UUID | None] = _fk("hotels")
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)  # user|guest|agent|system
    actor_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
