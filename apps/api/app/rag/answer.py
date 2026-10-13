from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import Settings
from app.rag.embeddings import EmbeddingProvider
from app.rag.retrieval import Audience, Passage, search
from app.rag.vectorstore import VectorStore

ABSTAIN_MESSAGE = ("I don't have verified information about that. I can connect you with our "
                   "front desk team who can help.")


@dataclass(frozen=True)
class Citation:
    chunk_id: UUID
    document_id: UUID
    title: str
    version: int
    heading: str


@dataclass
class KnowledgeAnswer:
    text: str
    abstained: bool
    reason: str  # answered | no_relevant_evidence
    citations: list[Citation] = field(default_factory=list)
    passages: list[Passage] = field(default_factory=list)


def _body(p: Passage) -> str:
    """Passage text without the heading line that the chunker prepends."""
    prefix = p.heading + "\n"
    return (p.text[len(prefix):] if p.heading and p.text.startswith(prefix) else p.text).strip()


def _is_relevant(p: Passage, settings: Settings) -> bool:
    return p.coverage >= settings.rag_min_coverage or p.cosine >= settings.rag_min_cosine


def answer_question(
    db: Session, *, settings: Settings, org_id, hotel_id, question: str, audience: Audience,
    embedder: EmbeddingProvider, store: VectorStore,
) -> KnowledgeAnswer:
    """Extractive, evidence-only answer. It quotes the hotel's own text and cites sources; it
    never generates facts. An LLM-based composer can wrap this later but must keep the same
    abstention gate and citations."""
    passages = search(db, org_id=org_id, hotel_id=hotel_id, query=question, audience=audience,
                      embedder=embedder, store=store, top_k=settings.rag_top_k)
    relevant = [p for p in passages if _is_relevant(p, settings)]
    if not relevant:
        return KnowledgeAnswer(ABSTAIN_MESSAGE, True, "no_relevant_evidence", [], passages)

    # Quote the best section verbatim (sentence-level extraction drops qualifiers such as
    # "subject to availability"). Add a second section only if it is independently relevant.
    chosen = [relevant[0]]
    for p in relevant[1:]:
        if len(chosen) == 2:
            break
        if (p.coverage >= max(settings.rag_min_coverage, 0.9 * chosen[0].coverage)
                and p.chunk_id != chosen[0].chunk_id):
            chosen.append(p)
    text = "\n\n".join(_body(p) for p in chosen)
    cites = [Citation(p.chunk_id, p.document_id, p.title, p.version, p.heading) for p in chosen]
    return KnowledgeAnswer(text, False, "answered", cites, relevant)
