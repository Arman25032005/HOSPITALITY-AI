import math
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import DocumentChunk, KnowledgeDocument
from app.rag.embeddings import ConfigError


@dataclass(frozen=True)
class SearchFilters:
    org_id: uuid.UUID
    hotel_id: uuid.UUID
    include_confidential: bool
    as_of: date
    embedding_model: str
    language: str | None = None
    doc_types: tuple[str, ...] | None = None


@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: uuid.UUID
    org_id: uuid.UUID
    hotel_id: uuid.UUID
    document_id: uuid.UUID
    vector: list[float]
    embedding_model: str
    doc_type: str
    language: str
    is_confidential: bool
    effective_ordinal: int  # YYYYMMDD, 0 when no effective date


@dataclass(frozen=True)
class VectorHit:
    chunk_id: uuid.UUID
    score: float


class VectorStore(Protocol):
    def upsert(self, chunks: list[IndexedChunk]) -> None: ...

    def delete_document(self, org_id: uuid.UUID, hotel_id: uuid.UUID,
                        document_id: uuid.UUID) -> None: ...

    def query(self, vector: list[float], filters: SearchFilters, top_k: int) -> list[VectorHit]: ...


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class LocalVectorStore:
    """Development/test store: vectors live on document_chunks and are scored in-process.

    Filtering happens in SQL before scoring. Fine for hundreds-to-low-thousands of chunks per
    hotel; use Pinecone beyond that. Retrieval re-verifies every hit against the database.
    """

    def __init__(self, db: Session):
        self.db = db

    def upsert(self, chunks: list[IndexedChunk]) -> None:
        for c in chunks:
            self.db.execute(update(DocumentChunk).where(DocumentChunk.id == c.chunk_id).values(
                embedding=c.vector, embedding_model=c.embedding_model))

    def delete_document(self, org_id, hotel_id, document_id) -> None:
        self.db.execute(update(DocumentChunk).where(
            DocumentChunk.document_id == document_id, DocumentChunk.organization_id == org_id,
            DocumentChunk.hotel_id == hotel_id).values(embedding=None, embedding_model=None))

    def query(self, vector, filters: SearchFilters, top_k: int) -> list[VectorHit]:
        q = eligible(select(DocumentChunk.id, DocumentChunk.embedding), filters).where(
            DocumentChunk.embedding.is_not(None),
            DocumentChunk.embedding_model == filters.embedding_model)
        scored = [VectorHit(cid, _dot(vector, emb)) for cid, emb in self.db.execute(q)
                  if len(emb) == len(vector)]
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]


def eligible(q, filters: SearchFilters):
    """Apply tenant, activity, audience and effective-date filters to a chunk/document query."""
    q = (q.join(KnowledgeDocument, KnowledgeDocument.id == DocumentChunk.document_id)
         .where(DocumentChunk.organization_id == filters.org_id,
                DocumentChunk.hotel_id == filters.hotel_id,
                DocumentChunk.suspicious.is_(False),
                KnowledgeDocument.is_active.is_(True),
                KnowledgeDocument.status == "indexed",
                (KnowledgeDocument.effective_date.is_(None))
                | (KnowledgeDocument.effective_date <= filters.as_of)))
    if not filters.include_confidential:
        q = q.where(KnowledgeDocument.is_confidential.is_(False))
    if filters.language:
        q = q.where(KnowledgeDocument.language == filters.language)
    if filters.doc_types:
        q = q.where(KnowledgeDocument.doc_type.in_(filters.doc_types))
    return q


def lexical_search(db: Session, filters: SearchFilters, terms: list[str],
                   limit: int) -> list[VectorHit]:
    """Full-text candidates from Postgres over pre-stemmed tokens (independent of the vector
    index, so exact-word matches are found even when the embedding misses them)."""
    terms = sorted({t for t in terms if t.isalnum()})  # tokens are [a-z0-9]; safe for tsquery
    if not terms:
        return []
    tsq = func.to_tsquery("simple", " | ".join(terms))
    tsv = func.to_tsvector("simple", DocumentChunk.search_tokens)
    rank = func.ts_rank_cd(tsv, tsq)
    q = eligible(select(DocumentChunk.id, rank), filters).where(tsv.op("@@")(tsq))
    rows = db.execute(q.order_by(rank.desc(), KnowledgeDocument.title, DocumentChunk.chunk_index)
                      .limit(limit)).all()
    return [VectorHit(cid, float(r)) for cid, r in rows]


class PineconeVectorStore:
    """Pinecone adapter. One namespace per (org, hotel) gives physical tenant separation.

    NOTE: exercised in tests against a fake index only; it has not been run against a live
    Pinecone project. Verify with real credentials before relying on it.
    """

    def __init__(self, index):
        self.index = index

    @staticmethod
    def namespace(org_id, hotel_id) -> str:
        return f"{org_id}:{hotel_id}"

    def upsert(self, chunks: list[IndexedChunk]) -> None:
        by_ns: dict[str, list[dict]] = {}
        for c in chunks:
            by_ns.setdefault(self.namespace(c.org_id, c.hotel_id), []).append({
                "id": str(c.chunk_id), "values": c.vector,
                "metadata": {"document_id": str(c.document_id), "embedding_model":
                             c.embedding_model, "doc_type": c.doc_type, "language": c.language,
                             "is_confidential": c.is_confidential,
                             "effective": c.effective_ordinal}})
        for ns, vectors in by_ns.items():
            for i in range(0, len(vectors), 100):
                self.index.upsert(vectors=vectors[i:i + 100], namespace=ns)

    def delete_document(self, org_id, hotel_id, document_id) -> None:
        self.index.delete(filter={"document_id": {"$eq": str(document_id)}},
                          namespace=self.namespace(org_id, hotel_id))

    def query(self, vector, filters: SearchFilters, top_k: int) -> list[VectorHit]:
        flt: dict = {"embedding_model": {"$eq": filters.embedding_model},
                     "effective": {"$lte": int(filters.as_of.strftime("%Y%m%d"))}}
        if not filters.include_confidential:
            flt["is_confidential"] = {"$eq": False}
        if filters.language:
            flt["language"] = {"$eq": filters.language}
        if filters.doc_types:
            flt["doc_type"] = {"$in": list(filters.doc_types)}
        res = self.index.query(vector=vector, top_k=top_k, filter=flt, include_metadata=False,
                               namespace=self.namespace(filters.org_id, filters.hotel_id))
        matches = res["matches"] if isinstance(res, dict) else res.matches
        out = []
        for m in matches:
            mid, score = (m["id"], m["score"]) if isinstance(m, dict) else (m.id, m.score)
            if math.isfinite(score):
                out.append(VectorHit(uuid.UUID(mid), float(score)))
        return out


def get_vector_store(settings: Settings, db: Session) -> VectorStore:
    if settings.vector_store_provider == "local":
        if settings.environment == "production":
            raise ConfigError("The local vector store is not permitted in production")
        return LocalVectorStore(db)
    if settings.vector_store_provider == "pinecone":
        if not settings.pinecone_api_key:
            raise ConfigError("VECTOR_STORE_PROVIDER=pinecone requires PINECONE_API_KEY")
        try:
            from pinecone import Pinecone
        except ImportError as exc:
            raise ConfigError("Pinecone support requires `pip install pinecone`") from exc
        return PineconeVectorStore(Pinecone(api_key=settings.pinecone_api_key)
                                   .Index(settings.pinecone_index))
    raise ConfigError(f"Unknown VECTOR_STORE_PROVIDER {settings.vector_store_provider!r}")
