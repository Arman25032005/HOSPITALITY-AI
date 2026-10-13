import hashlib
import re
import time
import uuid
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import DocumentChunk, IngestionJob, KnowledgeDocument
from app.rag.chunking import chunk_text
from app.rag.embeddings import EmbeddingProvider
from app.rag.parsers import UnparseableDocument, parse_document, validate_upload
from app.rag.safety import looks_like_injection
from app.rag.text import tokenize
from app.rag.vectorstore import IndexedChunk, VectorStore
from app.services import audit
from app.services.errors import ConflictError, DomainError, NotFoundError, ValidationFailed

DOC_TYPES = {"policy", "amenity", "room", "dining", "spa", "local", "faq", "internal", "other"}


class IngestionFailed(DomainError):
    status_code = 422


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:150] or "document"


def _eff_ordinal(d: date | None) -> int:
    return int(d.strftime("%Y%m%d")) if d else 0


def get_document(db: Session, *, org_id, hotel_id, document_id) -> KnowledgeDocument:
    doc = db.get(KnowledgeDocument, document_id)
    if doc is None or doc.organization_id != org_id or doc.hotel_id != hotel_id:
        raise NotFoundError("Document not found")
    return doc


def _build_chunks(db: Session, doc: KnowledgeDocument, text: str, embedder: EmbeddingProvider,
                  store: VectorStore) -> int:
    pieces = chunk_text(text, doc.title)
    if not pieces:
        raise UnparseableDocument("Document produced no chunks")
    rows = []
    for p in pieces:
        rows.append(DocumentChunk(
            organization_id=doc.organization_id, hotel_id=doc.hotel_id, document_id=doc.id,
            chunk_index=p.index, content=p.text, suspicious=looks_like_injection(p.text),
            search_tokens=" ".join(tokenize(p.text)),
            meta={"heading": p.heading, "doc_type": doc.doc_type, "language": doc.language,
                  "version": doc.version, "title": doc.title}))
    db.add_all(rows)
    db.flush()
    clean = [r for r in rows if not r.suspicious]
    if clean:
        vectors = embedder.embed_documents([r.content for r in clean])
        store.upsert([IndexedChunk(
            chunk_id=r.id, org_id=doc.organization_id, hotel_id=doc.hotel_id,
            document_id=doc.id, vector=v, embedding_model=embedder.name, doc_type=doc.doc_type,
            language=doc.language, is_confidential=doc.is_confidential,
            effective_ordinal=_eff_ordinal(doc.effective_date)) for r, v in zip(clean, vectors,
                                                                                strict=True)])
    return len(rows)


def ingest_document(
    db: Session, *, org_id: uuid.UUID, hotel_id: uuid.UUID, actor_id: str | None, filename: str,
    data: bytes, title: str, doc_type: str, embedder: EmbeddingProvider, store: VectorStore,
    max_bytes: int, language: str = "en", is_confidential: bool = False,
    doc_key: str | None = None, effective_date: date | None = None, is_demo: bool = False,
) -> KnowledgeDocument:
    title = title.strip()
    if not title:
        raise ValidationFailed("title is required")
    if doc_type not in DOC_TYPES:
        raise ValidationFailed(f"doc_type must be one of {sorted(DOC_TYPES)}")
    ext = validate_upload(filename, data, max_bytes)
    key = doc_key.strip() if doc_key and doc_key.strip() else _slug(title)
    sha = hashlib.sha256(data).hexdigest()

    current = db.scalar(select(KnowledgeDocument).where(
        KnowledgeDocument.hotel_id == hotel_id, KnowledgeDocument.doc_key == key,
        KnowledgeDocument.is_active.is_(True), KnowledgeDocument.status == "indexed"))
    if current and (current.content_sha256, current.title, current.doc_type, current.language,
                    current.is_confidential, current.effective_date) == (
            sha, title, doc_type, language, is_confidential, effective_date):
        return current  # identical re-upload: no new version

    version = (db.scalar(select(func.max(KnowledgeDocument.version)).where(
        KnowledgeDocument.hotel_id == hotel_id, KnowledgeDocument.doc_key == key)) or 0) + 1
    doc = KnowledgeDocument(
        organization_id=org_id, hotel_id=hotel_id, title=title, doc_type=doc_type,
        language=language, version=version, effective_date=effective_date, is_active=False,
        is_confidential=is_confidential, is_demo=is_demo, source=filename[:500], doc_key=key,
        status="processing", content_sha256=sha)
    db.add(doc)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ConflictError("A concurrent upload of this document is in progress; retry") from None
    job = IngestionJob(organization_id=org_id, hotel_id=hotel_id, document_id=doc.id,
                       status="running")
    db.add(job)
    db.commit()  # persist the attempt so failures are recorded

    started = time.monotonic()
    try:
        text = parse_document(ext, data)
        doc.content_text = text
        n = _build_chunks(db, doc, text, embedder, store)
        olds = list(db.scalars(select(KnowledgeDocument).where(
            KnowledgeDocument.hotel_id == hotel_id, KnowledgeDocument.doc_key == key,
            KnowledgeDocument.id != doc.id, KnowledgeDocument.is_active.is_(True))))
        for old in olds:
            old.is_active = False
        doc.is_active = True
        doc.status = "indexed"
        job.status = "succeeded"
        job.duration_ms = int((time.monotonic() - started) * 1000)
        audit.record(db, org_id=org_id, hotel_id=hotel_id, action="knowledge.ingested",
                     actor_type="user" if actor_id else "system", actor_id=actor_id,
                     entity_type="knowledge_document", entity_id=str(doc.id),
                     details={"key": key, "version": version, "chunks": n,
                              "replaced": [str(o.id) for o in olds]})
        db.commit()
    except Exception as exc:
        db.rollback()
        _record_failure(db, doc.id, job.id, exc, store, started)
        raise IngestionFailed(_public_error(exc)) from None
    for old in olds:
        store.delete_document(org_id, hotel_id, old.id)
    db.commit()
    return doc


def _public_error(exc: Exception) -> str:
    if isinstance(exc, UnparseableDocument):
        return f"Ingestion failed: {exc}"
    return "Ingestion failed due to an internal error; see ingestion job for the reference"


def _record_failure(db: Session, doc_id, job_id, exc: Exception, store: VectorStore,
                    started: float) -> None:
    doc = db.get(KnowledgeDocument, doc_id)
    job = db.get(IngestionJob, job_id)
    msg = str(exc) if isinstance(exc, UnparseableDocument) else f"{type(exc).__name__}"
    if doc:
        try:
            store.delete_document(doc.organization_id, doc.hotel_id, doc.id)
        except Exception:  # best-effort cleanup of partial vectors
            pass
        db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
        doc.status, doc.is_active = "failed", False
    if job:
        job.status, job.error = "failed", msg[:1000]
        job.duration_ms = int((time.monotonic() - started) * 1000)
    db.commit()


def reindex_document(db: Session, *, org_id, hotel_id, document_id, actor_id: str | None,
                     embedder: EmbeddingProvider, store: VectorStore) -> KnowledgeDocument:
    doc = get_document(db, org_id=org_id, hotel_id=hotel_id, document_id=document_id)
    if not doc.content_text:
        raise ConflictError("Document has no stored text to re-index")
    job = IngestionJob(organization_id=org_id, hotel_id=hotel_id, document_id=doc.id,
                       status="running")
    db.add(job)
    db.flush()
    started = time.monotonic()
    try:
        store.delete_document(org_id, hotel_id, doc.id)
        db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
        n = _build_chunks(db, doc, doc.content_text, embedder, store)
        doc.status = "indexed"
        job.status = "succeeded"
        job.duration_ms = int((time.monotonic() - started) * 1000)
        audit.record(db, org_id=org_id, hotel_id=hotel_id, action="knowledge.reindexed",
                     actor_type="user", actor_id=actor_id, entity_type="knowledge_document",
                     entity_id=str(doc.id), details={"chunks": n})
        db.commit()
    except Exception as exc:
        db.rollback()
        _record_failure(db, doc.id, job.id, exc, store, started)
        raise IngestionFailed(_public_error(exc)) from None
    return doc


def deactivate_document(db: Session, *, org_id, hotel_id, document_id,
                        actor_id: str | None, store: VectorStore) -> KnowledgeDocument:
    doc = get_document(db, org_id=org_id, hotel_id=hotel_id, document_id=document_id)
    doc.is_active = False
    audit.record(db, org_id=org_id, hotel_id=hotel_id, action="knowledge.deactivated",
                 actor_type="user", actor_id=actor_id, entity_type="knowledge_document",
                 entity_id=str(doc.id))
    db.commit()  # DB is the source of truth: retrieval re-verifies, so this takes effect now
    store.delete_document(org_id, hotel_id, doc.id)
    db.commit()
    return doc


def delete_document(db: Session, *, org_id, hotel_id, document_id, actor_id: str | None,
                    store: VectorStore) -> None:
    doc = get_document(db, org_id=org_id, hotel_id=hotel_id, document_id=document_id)
    doc.is_active = False
    db.commit()
    store.delete_document(org_id, hotel_id, doc.id)
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
    db.execute(delete(IngestionJob).where(IngestionJob.document_id == doc.id))
    audit.record(db, org_id=org_id, hotel_id=hotel_id, action="knowledge.deleted",
                 actor_type="user", actor_id=actor_id, entity_type="knowledge_document",
                 entity_id=str(doc.id), details={"key": doc.doc_key, "version": doc.version})
    db.delete(doc)
    db.commit()
