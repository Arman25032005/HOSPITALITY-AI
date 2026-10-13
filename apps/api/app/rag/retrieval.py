import logging
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DocumentChunk, KnowledgeDocument
from app.rag.embeddings import EmbeddingProvider
from app.rag.safety import looks_like_injection
from app.rag.text import tokenize
from app.rag.vectorstore import SearchFilters, VectorStore, lexical_search

log = logging.getLogger("hospitality.rag")
Audience = Literal["guest", "staff"]
RRF_K = 60


@dataclass(frozen=True)
class Passage:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    title: str
    doc_type: str
    version: int
    effective_date: date | None
    heading: str
    text: str
    cosine: float
    coverage: float  # fraction of query content terms present in the passage
    score: float  # fused rank score (relative; use cosine/coverage for absolute gating)


def term_coverage(query_terms: set[str], text: str) -> float:
    if not query_terms:
        return 0.0
    return len(query_terms & set(tokenize(text))) / len(query_terms)


def search(
    db: Session, *, org_id: uuid.UUID, hotel_id: uuid.UUID, query: str, audience: Audience,
    embedder: EmbeddingProvider, store: VectorStore, top_k: int = 5, hybrid: bool = True,
    language: str | None = None, doc_types: tuple[str, ...] | None = None,
    as_of: date | None = None,
) -> list[Passage]:
    query = query.strip()
    if not query:
        return []
    as_of = as_of or date.today()
    filters = SearchFilters(
        org_id=org_id, hotel_id=hotel_id, include_confidential=(audience == "staff"),
        as_of=as_of, embedding_model=embedder.name, language=language, doc_types=doc_types)
    n_cand = max(top_k * 4, 20)
    vec_hits = store.query(embedder.embed_query(query), filters, n_cand)
    lex_hits = lexical_search(db, filters, tokenize(query), n_cand) if hybrid else []
    if not vec_hits and not lex_hits:
        return []
    vec_rank = {h.chunk_id: i for i, h in enumerate(vec_hits)}
    lex_rank = {h.chunk_id: i for i, h in enumerate(lex_hits)}
    vec_score = {h.chunk_id: h.score for h in vec_hits}
    ids = set(vec_rank) | set(lex_rank)

    # Defence in depth: never trust the index's filtering. Re-check tenant, hotel, activity,
    # audience, effective date and injection status against the database.
    rows = db.execute(
        select(DocumentChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeDocument.id == DocumentChunk.document_id)
        .where(DocumentChunk.id.in_(ids))).all()
    q_terms = set(tokenize(query))
    cands = []
    for chunk, doc in rows:
        ok = (chunk.organization_id == org_id and chunk.hotel_id == hotel_id
              and doc.organization_id == org_id and doc.hotel_id == hotel_id
              and doc.is_active and doc.status == "indexed"
              and (doc.effective_date is None or doc.effective_date <= as_of)
              and (audience == "staff" or not doc.is_confidential)
              and not chunk.suspicious)
        if not ok:
            if chunk.organization_id != org_id or chunk.hotel_id != hotel_id:
                log.error("vector store returned out-of-scope chunk %s", chunk.id)
            continue
        if looks_like_injection(chunk.content):
            continue
        cands.append((chunk, doc, term_coverage(q_terms, chunk.content)))

    out = []
    for chunk, doc, cov in cands:
        fused = 0.0
        if chunk.id in vec_rank:
            fused += 1 / (RRF_K + vec_rank[chunk.id])
        if chunk.id in lex_rank:
            fused += 1 / (RRF_K + lex_rank[chunk.id])
        out.append(Passage(chunk.id, doc.id, doc.title, doc.doc_type, doc.version,
                           doc.effective_date, chunk.meta.get("heading", ""), chunk.content,
                           vec_score.get(chunk.id, 0.0), cov, fused))
    # Ties are common (RRF over small candidate lists); break them on stable content fields so
    # results do not depend on database row order or random chunk ids.
    out.sort(key=lambda p: (-p.score, -p.coverage, -p.cosine, p.title, p.heading, p.text))
    return out[:top_k]
