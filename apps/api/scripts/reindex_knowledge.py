"""Re-embed and re-index every active knowledge document (run after changing the embedder)."""
import sys

from sqlalchemy import select

from app.config import get_settings
from app.db.session import SessionLocal
from app.models import KnowledgeDocument
from app.rag.embeddings import get_embedder
from app.rag.ingestion import reindex_document
from app.rag.vectorstore import get_vector_store


def main() -> int:
    settings = get_settings()
    failures = 0
    with SessionLocal() as db:
        embedder, store = get_embedder(settings), get_vector_store(settings, db)
        docs = list(db.scalars(select(KnowledgeDocument).where(
            KnowledgeDocument.is_active.is_(True))))
        for d in docs:
            try:
                reindex_document(db, org_id=d.organization_id, hotel_id=d.hotel_id,
                                 document_id=d.id, actor_id=None, embedder=embedder, store=store)
            except Exception as exc:
                failures += 1
                print(f"FAILED {d.title}: {exc}", file=sys.stderr)
        print(f"Reindexed {len(docs) - failures}/{len(docs)} documents with {embedder.name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
