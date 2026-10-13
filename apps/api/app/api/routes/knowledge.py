import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.db.session import get_db
from app.models import DocumentChunk, IngestionJob, KnowledgeDocument, Role, User
from app.rag import answer as rag_answer
from app.rag import ingestion, retrieval
from app.rag.embeddings import EmbeddingProvider, get_embedder
from app.rag.vectorstore import VectorStore, get_vector_store
from app.schemas.knowledge import (
    AskBody, AskOut, CitationOut, DocumentDetail, DocumentOut, PassageOut, SearchBody,
)
from app.services.access import get_hotel_for_user, require_role

router = APIRouter(prefix="/api/v1/hotels/{hotel_id}/knowledge", tags=["knowledge"])

_embedder_cache: dict[tuple[str, str], EmbeddingProvider] = {}


def get_embedder_dep(settings: Settings = Depends(get_settings)) -> EmbeddingProvider:
    key = (settings.embedding_provider, settings.embedding_model)
    if key not in _embedder_cache:
        _embedder_cache[key] = get_embedder(settings)
    return _embedder_cache[key]


def get_store_dep(settings: Settings = Depends(get_settings),
                  db: Session = Depends(get_db)) -> VectorStore:
    return get_vector_store(settings, db)


ADMINS = (Role.admin, Role.manager)
STAFF = (Role.admin, Role.manager, Role.receptionist)


@router.post("/documents", response_model=DocumentOut, status_code=201)
async def upload_document(
    hotel_id: uuid.UUID, file: UploadFile = File(...), title: str = Form(..., max_length=300),
    doc_type: str = Form(...), language: str = Form("en", max_length=10),
    is_confidential: bool = Form(False), doc_key: str | None = Form(None, max_length=200),
    effective_date: date | None = Form(None), user: User = Depends(get_current_user),
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
    embedder: EmbeddingProvider = Depends(get_embedder_dep),
    store: VectorStore = Depends(get_store_dep),
):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *ADMINS)
    data = await file.read(settings.max_upload_bytes + 1)
    return ingestion.ingest_document(
        db, org_id=user.organization_id, hotel_id=hotel_id, actor_id=str(user.id),
        filename=file.filename or "upload", data=data, title=title, doc_type=doc_type,
        language=language, is_confidential=is_confidential, doc_key=doc_key,
        effective_date=effective_date, embedder=embedder, store=store,
        max_bytes=settings.max_upload_bytes)


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(hotel_id: uuid.UUID, active_only: bool = False,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *STAFF)
    q = select(KnowledgeDocument).where(KnowledgeDocument.organization_id == user.organization_id,
                                        KnowledgeDocument.hotel_id == hotel_id)
    if active_only:
        q = q.where(KnowledgeDocument.is_active.is_(True))
    return list(db.scalars(q.order_by(KnowledgeDocument.doc_key, KnowledgeDocument.version.desc())))


@router.get("/documents/{document_id}", response_model=DocumentDetail)
def document_detail(hotel_id: uuid.UUID, document_id: uuid.UUID,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *STAFF)
    doc = ingestion.get_document(db, org_id=user.organization_id, hotel_id=hotel_id,
                                 document_id=document_id)
    jobs = list(db.scalars(select(IngestionJob).where(IngestionJob.document_id == doc.id)
                           .order_by(IngestionJob.created_at.desc())))
    n = db.scalar(select(func.count()).select_from(DocumentChunk)
                  .where(DocumentChunk.document_id == doc.id)) or 0
    return DocumentDetail.model_validate({**DocumentOut.model_validate(doc).model_dump(),
                                          "chunk_count": n, "jobs": jobs})


@router.post("/documents/{document_id}/reindex", response_model=DocumentOut)
def reindex(hotel_id: uuid.UUID, document_id: uuid.UUID, user: User = Depends(get_current_user),
            db: Session = Depends(get_db), embedder: EmbeddingProvider = Depends(get_embedder_dep),
            store: VectorStore = Depends(get_store_dep)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *ADMINS)
    return ingestion.reindex_document(db, org_id=user.organization_id, hotel_id=hotel_id,
                                      document_id=document_id, actor_id=str(user.id),
                                      embedder=embedder, store=store)


@router.post("/documents/{document_id}/deactivate", response_model=DocumentOut)
def deactivate(hotel_id: uuid.UUID, document_id: uuid.UUID, user: User = Depends(get_current_user),
               db: Session = Depends(get_db), store: VectorStore = Depends(get_store_dep)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *ADMINS)
    return ingestion.deactivate_document(db, org_id=user.organization_id, hotel_id=hotel_id,
                                         document_id=document_id, actor_id=str(user.id),
                                         store=store)


@router.delete("/documents/{document_id}", status_code=204)
def delete(hotel_id: uuid.UUID, document_id: uuid.UUID, user: User = Depends(get_current_user),
           db: Session = Depends(get_db), store: VectorStore = Depends(get_store_dep)) -> None:
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *ADMINS)
    ingestion.delete_document(db, org_id=user.organization_id, hotel_id=hotel_id,
                              document_id=document_id, actor_id=str(user.id), store=store)


@router.post("/search", response_model=list[PassageOut])
def search(hotel_id: uuid.UUID, body: SearchBody, user: User = Depends(get_current_user),
           db: Session = Depends(get_db), embedder: EmbeddingProvider = Depends(get_embedder_dep),
           store: VectorStore = Depends(get_store_dep)):
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *STAFF)
    return retrieval.search(db, org_id=user.organization_id, hotel_id=hotel_id, query=body.query,
                            audience=body.audience, embedder=embedder, store=store,
                            top_k=body.top_k, language=body.language)


@router.post("/ask", response_model=AskOut)
def ask(hotel_id: uuid.UUID, body: AskBody, user: User = Depends(get_current_user),
        db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
        embedder: EmbeddingProvider = Depends(get_embedder_dep),
        store: VectorStore = Depends(get_store_dep)):
    """Staff preview of what the knowledge concierge would answer for a guest."""
    get_hotel_for_user(db, user, hotel_id)
    require_role(user, *STAFF)
    res = rag_answer.answer_question(
        db, settings=settings, org_id=user.organization_id, hotel_id=hotel_id,
        question=body.question, audience=body.audience, embedder=embedder, store=store)
    return AskOut(answer=res.text, abstained=res.abstained, reason=res.reason,
                  citations=[CitationOut(**c.__dict__) for c in res.citations])
